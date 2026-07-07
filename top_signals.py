"""일/주/월 종목·섹터 TOP10 빈도 + '어떤 뷰로 이야기되는지' 브리프.

매일 아침(KST 8시 전) 갱신. 그날 해당되는 주기를 자동 산출한다.
- daily   : 매일            (최근 1일)
- weekly  : 토요일          (최근 7일 — 한 주 마감)
- monthly : 매월 1일        (최근 30일 — 직전 달 결산)

각 주기마다 두 차원을 TOP10으로:
  ① 종목  ② 섹터
빈도는 '교차' — 텔레그램 회자수(messages) + 애널리스트 리포트 빈도(sent_reports)를
함께 보여주고, 텔레그램 회자수를 1순위로 랭크한다. 각 항목엔 LLM이 만든 1~2줄
'시장 시각(강세/약세/중립 + 핵심 논지)'을 붙인다.

데이터 출처(읽기 전용):
  messages          텔레그램 원문 → 회자수 + 뷰 샘플
  sent_reports      증권사 리포트 빈도 (stock_name=종목/섹터)
  ticker_consensus  종목 투자의견·목표가 → 종목 뷰 근거
  sector_synthesis  섹터 overview/themes → 섹터 뷰 근거 + 섹터 유니버스

적재: top_signal_briefs (reportportal 대시보드용) + 텔레그램 봇 게시.

사용:
    python top_signals.py                  # 오늘 해당 주기 자동 (DB적재+게시)
    python top_signals.py --period daily   # 특정 주기 강제
    python top_signals.py --period weekly --no-post     # 게시 생략 (검증)
    python top_signals.py --period daily --dry-run      # 적재·게시 모두 생략, 출력만
"""
import argparse
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from openai import OpenAI

import config
import db
import bot_poster
from tele_signals import count_matches, stock_aliases, SECTOR_LIKE

KST = ZoneInfo("Asia/Seoul")
client = OpenAI(api_key=config.OPENAI_API_KEY)

# 주기 → 윈도우(일)
PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
PERIOD_KR = {"daily": "일간", "weekly": "주간", "monthly": "월간"}
TOP_N = 10

# 종목·섹터 어느 쪽도 아닌 일반 토큰 — 랭킹에서 제외 (의미 없는 '전체/기타/시황' 등)
GENERIC = {"전체", "기타", "시황", "전체시장", "코스피", "코스닥", "시장", "종합"}


# ── 주기 판단 ────────────────────────────────────────────────────────────────
def due_periods(today: datetime) -> list[str]:
    """오늘(KST) 산출해야 할 주기 목록. 일간은 항상, 주간은 토요일, 월간은 1일."""
    out = ["daily"]
    if today.weekday() == 5:      # 토요일 = 한 주 마감
        out.append("weekly")
    if today.day == 1:            # 매월 1일 = 직전 달 결산
        out.append("monthly")
    return out


# ── 스키마 ───────────────────────────────────────────────────────────────────
def ensure_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS top_signal_briefs (
            id           BIGSERIAL PRIMARY KEY,
            period       TEXT NOT NULL,          -- daily|weekly|monthly
            dimension    TEXT NOT NULL,          -- stock|sector
            rank         INTEGER NOT NULL,
            name         TEXT NOT NULL,
            tele_count   INTEGER NOT NULL DEFAULT 0,
            report_count INTEGER NOT NULL DEFAULT 0,
            view         TEXT,
            as_of        DATE NOT NULL,
            window_days  INTEGER NOT NULL,
            generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (period, dimension, rank, as_of)
        )""")
    conn.execute("""
        CREATE INDEX IF NOT EXISTS top_signal_briefs_lookup_idx
        ON top_signal_briefs (period, dimension, as_of DESC, rank)""")


# ── 데이터 로드 ──────────────────────────────────────────────────────────────
def load_texts(conn, days: int) -> list[str]:
    rows = conn.execute(
        "SELECT text FROM messages "
        "WHERE posted_at >= NOW() - make_interval(days => %s) AND text IS NOT NULL",
        (days,),
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def sector_universe(conn) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT name FROM sector_synthesis WHERE lang='ko' AND name IS NOT NULL"
    ).fetchall()
    return {(n or "").strip() for (n,) in rows if (n or "").strip()} | SECTOR_LIKE


def stock_universe(conn, days: int, sectors: set[str]) -> list[str]:
    """리포트에 등장한 distinct 종목(섹터명 제외). 윈도우가 짧아도 최소 14일치 유니버스."""
    rows = conn.execute(
        "SELECT DISTINCT stock_name FROM sent_reports "
        "WHERE report_date >= (NOW()::date - %s) AND stock_name IS NOT NULL",
        (max(days, 14),),
    ).fetchall()
    out = []
    for (n,) in rows:
        n = (n or "").strip()
        if n and n not in sectors and n not in GENERIC:
            out.append(n)
    return out


def report_counts(conn, days: int) -> dict[str, int]:
    rows = conn.execute(
        "SELECT stock_name, COUNT(*) FROM sent_reports "
        "WHERE report_date >= (NOW()::date - %s) AND stock_name IS NOT NULL "
        "GROUP BY stock_name",
        (days,),
    ).fetchall()
    return {(n or "").strip(): int(c) for n, c in rows}


def consensus_map(conn, names: list[str]) -> dict[str, dict]:
    if not names:
        return {}
    rows = conn.execute(
        "SELECT stock_name, rating_dist, target_avg, synthesis_text "
        "FROM ticker_consensus WHERE stock_name = ANY(%s)",
        (names,),
    ).fetchall()
    out = {}
    for name, rating, tgt, syn in rows:
        out[name] = {
            "rating_dist": rating,
            "target_avg": float(tgt) if tgt is not None else None,
            "synthesis": (syn or "")[:400] or None,
        }
    return out


def sector_context_map(conn, names: list[str]) -> dict[str, dict]:
    if not names:
        return {}
    rows = conn.execute(
        "SELECT DISTINCT ON (name) name, overview, themes FROM sector_synthesis "
        "WHERE lang='ko' AND name = ANY(%s) ORDER BY name, generated_at DESC",
        (names,),
    ).fetchall()
    return {n: {"overview": (o or "")[:400] or None, "themes": th} for n, o, th in rows}


# ── 빈도 랭킹 ────────────────────────────────────────────────────────────────
def rank_items(names: list[str], texts: list[str], rep_cnt: dict[str, int]) -> list[dict]:
    """텔레그램 회자수 1순위, 리포트 빈도 2순위로 TOP_N. 둘 다 0이면 제외."""
    items = []
    for name in names:
        tele = count_matches(stock_aliases(name), texts)
        rep = rep_cnt.get(name, 0)
        if tele > 0 or rep > 0:
            items.append({"name": name, "tele": tele, "report": rep})
    items.sort(key=lambda x: (x["tele"], x["report"]), reverse=True)
    return items[:TOP_N]


def sample_messages(name: str, texts: list[str], k: int = 3) -> list[str]:
    aliases = [a for a, _ in stock_aliases(name)]
    out = []
    for t in texts:
        if any(a in t for a in aliases):
            out.append(t[:200])
            if len(out) >= k:
                break
    return out


# ── LLM 뷰 (배치) ────────────────────────────────────────────────────────────
STOCK_VIEW_PROMPT = """너는 한국 주식 sell-side 애널리스트다. 아래 JSON 배열은 최근 기간 시장에서
많이 회자된 종목들과, 각 종목의 컨센서스/리포트 수/텔레그램 원문 샘플이다.
각 종목마다 '시장이 지금 어떤 시각으로 이 종목을 이야기하는지'를 한국어 1~2줄(최대 60자)로 요약하라.

규칙:
- 맨 앞에 톤 라벨을 붙여라: 강세/약세/중립/혼조 중 하나.
- 핵심 논지(촉매 또는 우려)를 사실·숫자 중심으로. 데이터에 없는 수치 창작 금지.
- 채널명·매체명·메타표현 금지.
- 출력은 오직 JSON 객체: {"종목명": "강세 — ...", ...}. 입력의 모든 종목을 포함하라."""

SECTOR_VIEW_PROMPT = """너는 한국 주식 섹터 전략가다. 아래 JSON 배열은 최근 기간 많이 회자된 섹터들과,
각 섹터의 합성 개요/테마/텔레그램 원문 샘플이다.
각 섹터마다 '시장이 지금 이 섹터를 어떤 시각으로 이야기하는지'를 한국어 1~2줄(최대 60자)로 요약하라.

규칙:
- 맨 앞에 톤 라벨: 강세/약세/중립/혼조 중 하나.
- 핵심 흐름(주도 테마·촉매·우려)을 사실 중심으로. 데이터에 없는 사실 창작 금지.
- 채널명·매체명·메타표현 금지.
- 출력은 오직 JSON 객체: {"섹터명": "강세 — ...", ...}. 입력의 모든 섹터를 포함하라."""


def _parse_json_obj(raw: str) -> dict:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {}
    except json.JSONDecodeError:
        return {}


def generate_views(system_prompt: str, payload: list[dict]) -> dict[str, str]:
    if not payload:
        return {}
    resp = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
        ],
        max_completion_tokens=1500,
        reasoning_effort="low",
    )
    return _parse_json_obj(resp.choices[0].message.content or "")


# ── 한 차원(종목/섹터) 산출 ──────────────────────────────────────────────────
def build_stocks(conn, texts: list[str], days: int) -> list[dict]:
    sectors = sector_universe(conn)
    universe = stock_universe(conn, days, sectors)
    rep = report_counts(conn, days)
    top = rank_items(universe, texts, rep)
    cons = consensus_map(conn, [t["name"] for t in top])
    payload = []
    for it in top:
        c = cons.get(it["name"], {})
        payload.append({
            "종목": it["name"],
            "텔레그램_회자수": it["tele"],
            "리포트_수": it["report"],
            "투자의견분포": c.get("rating_dist"),
            "목표가평균": c.get("target_avg"),
            "컨센서스코멘트": c.get("synthesis"),
            "텔레그램샘플": sample_messages(it["name"], texts),
        })
    views = generate_views(STOCK_VIEW_PROMPT, payload)
    for it in top:
        it["view"] = views.get(it["name"], "")
    return top


def build_sectors(conn, texts: list[str], days: int) -> list[dict]:
    sectors = sorted(sector_universe(conn) - GENERIC)
    rep = report_counts(conn, days)  # 섹터 레벨 리포트(stock_name=섹터)도 여기 포함됨
    top = rank_items(sectors, texts, rep)
    ctx = sector_context_map(conn, [t["name"] for t in top])
    payload = []
    for it in top:
        c = ctx.get(it["name"], {})
        payload.append({
            "섹터": it["name"],
            "텔레그램_회자수": it["tele"],
            "리포트_수": it["report"],
            "섹터개요": c.get("overview"),
            "테마": c.get("themes"),
            "텔레그램샘플": sample_messages(it["name"], texts),
        })
    views = generate_views(SECTOR_VIEW_PROMPT, payload)
    for it in top:
        it["view"] = views.get(it["name"], "")
    return top


# ── 적재 ─────────────────────────────────────────────────────────────────────
def save(conn, period: str, as_of, days: int, stocks: list[dict], sectors: list[dict]):
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM top_signal_briefs WHERE period=%s AND as_of=%s",
            (period, as_of),
        )
        rows = []
        for dim, items in (("stock", stocks), ("sector", sectors)):
            for rank, it in enumerate(items, 1):
                rows.append((period, dim, rank, it["name"], it["tele"],
                             it["report"], it.get("view") or None, as_of, days))
        if rows:
            cur.executemany(
                "INSERT INTO top_signal_briefs "
                "(period, dimension, rank, name, tele_count, report_count, view, as_of, window_days) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                rows,
            )


# ── 렌더 ─────────────────────────────────────────────────────────────────────
def render(period: str, as_of, days: int, stocks: list[dict], sectors: list[dict]) -> str:
    head = f"📊 {PERIOD_KR[period]} 시그널 TOP10  ({as_of} 기준 · 최근 {days}일)"
    lines = [head, ""]

    def block(title: str, items: list[dict]):
        lines.append(title)
        if not items:
            lines.append("  (해당 없음)")
        for i, it in enumerate(items, 1):
            lines.append(f"{i:2}. {it['name']}  🗣️{it['tele']} 📄{it['report']}")
            if it.get("view"):
                lines.append(f"    └ {it['view']}")
        lines.append("")

    block("📈 종목 TOP 10", stocks)
    block("🏭 섹터 TOP 10", sectors)
    lines.append("🗣️ 텔레그램 회자수 · 📄 리포트 수")
    return "\n".join(lines)


# ── 실행 ─────────────────────────────────────────────────────────────────────
def run_period(conn, period: str, today, *, post: bool, persist: bool) -> str:
    days = PERIOD_DAYS[period]
    texts = load_texts(conn, days)
    as_of = today.date()
    stocks = build_stocks(conn, texts, days)
    sectors = build_sectors(conn, texts, days)
    report = render(period, as_of, days, stocks, sectors)
    print(report)
    print()
    if persist:
        save(conn, period, as_of, days, stocks, sectors)
        print(f"[{period}] DB 적재 완료 (종목 {len(stocks)}·섹터 {len(sectors)})")
    if post:
        bot_poster.post(report, kind="top_signals", meta={"period": period})
        print(f"[{period}] 텔레그램 게시 완료")
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--period", choices=list(PERIOD_DAYS), help="강제 주기 (생략 시 오늘 해당분 자동)")
    p.add_argument("--no-post", action="store_true", help="텔레그램 게시 생략 (DB는 적재)")
    p.add_argument("--dry-run", action="store_true", help="DB 적재·게시 모두 생략, 출력만")
    args = p.parse_args()

    today = datetime.now(KST)
    periods = [args.period] if args.period else due_periods(today)
    print(f"top_signals 시작: {today:%Y-%m-%d %H:%M} KST · 주기={periods}")

    with db.connection() as conn:
        ensure_schema(conn)
        for period in periods:
            run_period(
                conn, period, today,
                post=not (args.no_post or args.dry_run),
                persist=not args.dry_run,
            )
            print("=" * 60)
    db.close()
    print("완료.")


if __name__ == "__main__":
    main()
