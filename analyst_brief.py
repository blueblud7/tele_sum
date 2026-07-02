"""종목 애널리스트 브리프 생성 (읽기 위주 + 자체 테이블에만 적재).

이미 Neon에 쌓여 있는 데이터를 한 종목 기준으로 조립해 애널리스트 브리프를 만든다.
- ticker_consensus : 증권사 목표가/투자의견 컨센서스 + 종합코멘트
- messages         : 최근 텔레그램 원문 시그널 (종목 언급)
- summaries        : 최근 다이제스트 (종목 언급)
- sector_synthesis : 섹터 맥락
- cio_brief        : 당일 매크로/CIO 브리프

하이브리드 양쪽을 한 진입점으로:
  python analyst_brief.py "SK하이닉스"   # 온디맨드 (생성+저장+출력)
  python analyst_brief.py                # 워치리스트 일괄 (스케줄용)
  python analyst_brief.py --list         # 워치리스트 조회
  python analyst_brief.py --add "삼성전자"   # 워치리스트 추가 (ticker/sector 자동 해석)
  python analyst_brief.py --remove "삼성전자"

기존 서비스/테이블은 건드리지 않음. 신규 테이블(analyst_watchlist, analyst_brief)만
CREATE IF NOT EXISTS로 보장하고, 거기에만 INSERT 한다.
"""
import sys
import re
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv
from openai import OpenAI

import config

load_dotenv(".env", override=True)
DATABASE_URL = os.getenv("DATABASE_URL")
openai_client = OpenAI(api_key=config.OPENAI_API_KEY)

# DB 워치리스트가 비었을 때의 폴백.
DEFAULT_WATCHLIST = ["SK하이닉스"]

# 종목 → 섹터 키워드 (sector_synthesis 매칭 + 워치리스트 sector 기본값).
SECTOR_HINT = {
    "SK하이닉스": "반도체",
    "삼성전자": "반도체",
    "에코프로비엠": "2차전지",
    "삼성SDI": "2차전지",
    "HD한국조선해양": "조선",
    "한화오션": "조선",
}

RECENT_DAYS = 14
MAX_MSGS = 25
MAX_SUMMARIES = 8


# ── DB 헬퍼 ────────────────────────────────────────────────────────────────
def _conn():
    return psycopg.connect(DATABASE_URL)


def _fetchall(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def ensure_schema():
    """신규 테이블 보장 (기존 테이블에 영향 없음)."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS analyst_watchlist (
                stock_name TEXT PRIMARY KEY,
                ticker     TEXT,
                sector     TEXT,
                active     BOOLEAN DEFAULT TRUE,
                added_at   TIMESTAMPTZ DEFAULT now()
            )""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS analyst_brief (
                id             BIGSERIAL PRIMARY KEY,
                stock_name     TEXT NOT NULL,
                content        TEXT NOT NULL,
                consensus_asof DATE,
                n_messages     INT,
                n_summaries    INT,
                model          TEXT,
                generated_at   TIMESTAMPTZ DEFAULT now()
            )""")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_analyst_brief_stock
            ON analyst_brief (stock_name, generated_at DESC)""")
        # 컨센서스 변화 추적용 히스토리. 컨센서스가 실제로 갱신될 때(generated_at 변경)만 1행.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ticker_consensus_history (
                ticker         TEXT NOT NULL,
                stock_name     TEXT,
                broker_count   INT,
                rating_dist    JSONB,
                target_min     NUMERIC,
                target_max     NUMERIC,
                target_avg     NUMERIC,
                target_median  NUMERIC,
                report_count   INT,
                consensus_generated_at TIMESTAMPTZ NOT NULL,
                snapshotted_at TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (ticker, consensus_generated_at)
            )""")
        conn.commit()


def resolve_ticker_sector(stock: str) -> tuple[str | None, str | None]:
    """ticker_consensus에서 ticker 해석, sector는 힌트맵 사용."""
    ticker = None
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT ticker FROM ticker_consensus WHERE stock_name=%s", (stock,))
        row = cur.fetchone()
        if row:
            ticker = row[0]
    return ticker, SECTOR_HINT.get(stock)


def load_watchlist() -> list[str]:
    with _conn() as conn, conn.cursor() as cur:
        rows = _fetchall(cur,
            "SELECT stock_name FROM analyst_watchlist WHERE active ORDER BY added_at")
    names = [r["stock_name"] for r in rows]
    return names or DEFAULT_WATCHLIST


def add_watchlist(stock: str):
    ticker, sector = resolve_ticker_sector(stock)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO analyst_watchlist (stock_name, ticker, sector, active)
            VALUES (%s, %s, %s, TRUE)
            ON CONFLICT (stock_name) DO UPDATE
              SET active=TRUE, ticker=EXCLUDED.ticker, sector=EXCLUDED.sector
        """, (stock, ticker, sector))
        conn.commit()
    print(f"+ 워치리스트 추가: {stock} (ticker={ticker}, sector={sector})")


def remove_watchlist(stock: str):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE analyst_watchlist SET active=FALSE WHERE stock_name=%s", (stock,))
        conn.commit()
    print(f"- 워치리스트 비활성화: {stock}")


def list_watchlist():
    with _conn() as conn, conn.cursor() as cur:
        rows = _fetchall(cur,
            "SELECT stock_name, ticker, sector, active, added_at "
            "FROM analyst_watchlist ORDER BY active DESC, added_at")
    if not rows:
        print("(워치리스트 비어있음 — 폴백:", DEFAULT_WATCHLIST, ")")
        return
    for r in rows:
        flag = "✓" if r["active"] else "·"
        print(f"  {flag} {r['stock_name']:12} {r['ticker'] or '-':8} {r['sector'] or '-'}")


def save_brief(data: dict, content: str):
    cons = data["consensus"]
    asof = cons["generated_at"].date() if cons and cons.get("generated_at") else None
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO analyst_brief
              (stock_name, content, consensus_asof, n_messages, n_summaries, model)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (data["stock"], content, asof,
              len(data["messages"]), len(data["summaries"]), config.OPENAI_MODEL))
        conn.commit()


# ── 컨센서스 변화 추적 (#3) ────────────────────────────────────────────────
def snapshot_consensus() -> int:
    """현재 ticker_consensus 전체를 히스토리에 적재. generated_at 동일하면 무시.
    컨센서스가 갱신될 때만 새 행이 쌓이므로 진짜 '변화'만 기록된다."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO ticker_consensus_history
              (ticker, stock_name, broker_count, rating_dist, target_min, target_max,
               target_avg, target_median, report_count, consensus_generated_at)
            SELECT ticker, stock_name, broker_count, rating_dist, target_min, target_max,
                   target_avg, target_median, report_count, generated_at
            FROM ticker_consensus
            WHERE generated_at IS NOT NULL AND ticker IS NOT NULL
            ON CONFLICT (ticker, consensus_generated_at) DO NOTHING
        """)
        n = cur.rowcount
        conn.commit()
    return n


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _delta(old, new):
    """(old, new, pct_change|None) — 비교 가능할 때만 pct."""
    o, n = _num(old), _num(new)
    pct = round((n - o) / o * 100, 1) if (o not in (None, 0) and n is not None) else None
    return {"old": o, "new": n, "pct": pct}


def consensus_change(stock: str) -> dict | None:
    """해당 종목의 최근 두 컨센서스 시점을 비교. 히스토리가 1개뿐이면 first=True."""
    with _conn() as conn, conn.cursor() as cur:
        rows = _fetchall(cur,
            "SELECT consensus_generated_at, broker_count, report_count, rating_dist, "
            "target_avg, target_median, target_min, target_max "
            "FROM ticker_consensus_history WHERE stock_name=%s "
            "ORDER BY consensus_generated_at DESC LIMIT 2", (stock,))
    if not rows:
        return None
    if len(rows) == 1:
        return {"first": True, "curr_asof": str(rows[0]["consensus_generated_at"])[:10]}
    cur_, prev = rows[0], rows[1]
    return {
        "first": False,
        "prev_asof": str(prev["consensus_generated_at"])[:10],
        "curr_asof": str(cur_["consensus_generated_at"])[:10],
        "target_avg": _delta(prev["target_avg"], cur_["target_avg"]),
        "target_median": _delta(prev["target_median"], cur_["target_median"]),
        "broker_count": _delta(prev["broker_count"], cur_["broker_count"]),
        "report_count": _delta(prev["report_count"], cur_["report_count"]),
        "rating_prev": prev["rating_dist"],
        "rating_curr": cur_["rating_dist"],
    }


def render_change(ch: dict | None) -> str:
    """컨센서스 변화를 '한 줄 요약'으로 압축 (숫자는 LLM 거치지 않음 · 결정적)."""
    if not ch:
        return ""
    if ch.get("first"):
        return f"## 컨센서스 변화\n- 첫 스냅샷({ch['curr_asof']}) — 다음 갱신부터 변동 추적\n"

    parts = []

    def pct_part(label, d):
        if d["new"] is None or d["pct"] is None:
            return None
        arrow = "▲" if d["pct"] > 0 else ("▼" if d["pct"] < 0 else "—")
        return f"{label} {arrow}{abs(d['pct'])}%"

    for label, key in [("목표가 평균", "target_avg"), ("중앙값", "target_median")]:
        p = pct_part(label, ch[key])
        if p:
            parts.append(p)
    for label, key, unit in [("리포트", "report_count", "건"), ("증권사", "broker_count", "곳")]:
        d = ch[key]
        if d["new"] is not None and d["old"] is not None:
            diff = int(d["new"]) - int(d["old"])
            if diff:
                parts.append(f"{label} {diff:+d}{unit}")

    summary = " · ".join(parts) if parts else "유의미한 변동 없음"
    return f"## 컨센서스 변화 ({ch['prev_asof']} → {ch['curr_asof']})\n- {summary}\n"


# ── 데이터 수집 + 브리프 생성 ─────────────────────────────────────────────
def gather(stock: str) -> dict:
    since = datetime.now(ZoneInfo("Asia/Seoul")) - timedelta(days=RECENT_DAYS)
    short = stock.replace("(주)", "").strip()
    like_short = f"%{short[:4]}%" if len(short) >= 4 else f"%{short}%"

    out = {"stock": stock, "consensus": None, "messages": [],
           "summaries": [], "sector": None, "cio": None}

    with _conn() as conn, conn.cursor() as cur:
        rows = _fetchall(cur,
            "SELECT stock_name,ticker,broker_count,rating_dist,target_min,target_max,"
            "target_avg,target_median,report_count,synthesis_text,generated_at "
            "FROM ticker_consensus WHERE stock_name=%s", (stock,))
        out["consensus"] = rows[0] if rows else None

        out["messages"] = _fetchall(cur,
            "SELECT posted_at, text FROM messages "
            "WHERE text ILIKE %s AND posted_at >= %s "
            "ORDER BY posted_at DESC LIMIT %s", (like_short, since, MAX_MSGS))

        out["summaries"] = _fetchall(cur,
            "SELECT created_at, kind, content FROM summaries "
            "WHERE content ILIKE %s AND created_at >= %s "
            "ORDER BY created_at DESC LIMIT %s", (like_short, since, MAX_SUMMARIES))

        hint = SECTOR_HINT.get(stock)
        if hint:
            secs = _fetchall(cur,
                "SELECT name, overview, themes, generated_at FROM sector_synthesis "
                "WHERE name ILIKE %s AND lang='ko' ORDER BY generated_at DESC LIMIT 1",
                (f"%{hint}%",))
            out["sector"] = secs[0] if secs else None

        cio = _fetchall(cur,
            "SELECT date, content FROM cio_brief ORDER BY date DESC LIMIT 1")
        out["cio"] = cio[0] if cio else None

    return out


BRIEF_PROMPT = """너는 한국 주식 sell-side 애널리스트다. 아래 구조화 데이터만 근거로
'{stock}'에 대한 간결한 애널리스트 브리프를 한국어로 작성하라.

다음 섹션을 마크다운으로:
## 한 줄 요약
- 투자의견 톤과 핵심 메시지 1~2문장.

## 컨센서스
- 증권사 수, 목표가(평균/중앙/범위), 투자의견 분포를 사실 그대로. 숫자는 원 단위로 표기.

## 최근 동향
- 최근 텔레그램/다이제스트에서 나온 이 종목 관련 시그널을 3~5개 bullet. 사실·숫자 중심.
- 컨센서스 코멘트와 비교해 '새로 바뀐 점/촉매'가 보이면 명시.

## 섹터 맥락
- 섹터 합성에서 이 종목에 직접 관련된 흐름만 1~3문장.

## 리스크
- 데이터에 등장한 리스크만 bullet.

규칙:
- 데이터에 없는 수치·사실 창작 금지. 모르면 '데이터 없음'.
- 채널명·매체명 금지. 메타표현("요약하면" 등) 금지.
- 전체 25줄 이내.
"""


def build_brief(data: dict) -> str:
    stock = data["stock"]
    if not data["consensus"] and not data["messages"] and not data["summaries"]:
        return f"# {stock}\n\n수집된 데이터가 없습니다. (Neon에 해당 종목 언급/컨센서스 없음)"

    payload = {
        "consensus": data["consensus"],
        "recent_messages": [
            {"date": str(m["posted_at"])[:16], "text": (m["text"] or "")[:300]}
            for m in data["messages"]
        ],
        "recent_summaries": [
            {"date": str(s["created_at"])[:16], "content": (s["content"] or "")[:600]}
            for s in data["summaries"]
        ],
        "sector": data["sector"],
        "cio_brief": (data["cio"] or {}).get("content", "")[:800] if data["cio"] else None,
    }
    user = json.dumps(payload, ensure_ascii=False, default=str)

    resp = openai_client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": BRIEF_PROMPT.format(stock=stock)},
            {"role": "user", "content": user},
        ],
        max_completion_tokens=1200,
        reasoning_effort="low",
    )
    body = (resp.choices[0].message.content or "").strip()
    cons = data["consensus"]
    asof = str(cons["generated_at"])[:10] if cons else "?"
    n_msg, n_sum = len(data["messages"]), len(data["summaries"])
    footer = (f"\n\n---\n_근거: 컨센서스 as-of {asof} · 최근 메시지 {n_msg}건 · "
              f"다이제스트 {n_sum}건 (최근 {RECENT_DAYS}일)_")

    # 결정적 변화 블록을 제목 바로 뒤에 주입 (숫자 신뢰성).
    change_md = render_change(consensus_change(stock))
    head = f"# 📊 {stock} 애널리스트 브리프\n\n"
    if change_md:
        head += change_md + "\n"
    return f"{head}{body}{footer}"


def to_telegram(md: str) -> str:
    """브리프 마크다운을 채널 평문(이모지+불릿) 스타일로 변환.
    봇이 parse_mode 없이 보내므로 #, **, _ 같은 마크업이 글자로 노출되는 걸 막는다."""
    out = []
    for line in md.splitlines():
        if line.strip() == "---":
            out.append("━━━━━━━━━━")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level, title = len(m.group(1)), m.group(2)
            if level == 1:
                out.append(title)          # 제목 (이미 📊 포함)
            else:
                out.append("")             # 섹션 앞 빈 줄
                out.append(f"▎{title}")    # 섹션 헤더
            continue
        out.append(re.sub(r"^(\s*)[-*]\s+", r"\1• ", line))  # 불릿 → •
    text = "\n".join(out)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)              # **굵게** 제거
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)       # _기울임_ 제거
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def run_for(stock: str, post: bool = False):
    data = gather(stock)
    content = build_brief(data)
    print(content)
    save_brief(data, content)
    if post:
        if not (data["consensus"] or data["messages"] or data["summaries"]):
            print("[post 스킵] 수집 데이터 없음")
        else:
            import bot_poster
            bot_poster.post(to_telegram(content))
            print("[post] 채널 발행 완료")


def main():
    ensure_schema()
    args = sys.argv[1:]

    # --post: 생성한 브리프를 채널에도 발행 (기본은 DB 적재만).
    post = "--post" in args
    args = [a for a in args if a != "--post"]

    # 매 실행마다 현 컨센서스를 히스토리에 적재 (갱신분만 새 행). 온디맨드 종목도 누적됨.
    if not args or args[0] not in ("--list", "--add", "--remove"):
        n = snapshot_consensus()
        if n:
            print(f"[변화추적] 컨센서스 히스토리 {n}행 신규 적재\n")

    if args and args[0] == "--list":
        list_watchlist(); return
    if args and args[0] == "--add":
        add_watchlist(" ".join(args[1:]).strip()); return
    if args and args[0] == "--remove":
        remove_watchlist(" ".join(args[1:]).strip()); return

    targets = [" ".join(args).strip()] if args else load_watchlist()
    for stock in targets:
        run_for(stock, post=post)
        print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    main()
