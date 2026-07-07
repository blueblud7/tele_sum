"""주간 track record 회고 — '지난주 우리가 먼저 짚은 종목'.

지난 7일간 이 채널이 가장 많이 포착한 종목과, 그 사이 시장(증권사 컨센서스/리포트)이
어떻게 반응했는지를 '우리 데이터'만으로 보여주는 주간 결산. 채널의 '먼저 포착' 가치를
증명해 공유·구독을 유도한다. (주가/수익률 예측 주장 없음 — 검증 가능한 수치만.)

데이터 출처(읽기 전용):
  messages                  텔레그램 회자수
  sent_reports              리포트 follow-through (report_counts)
  ticker_consensus_history  주간 컨센서스 목표가 변화 (analyst_brief가 매일 스냅샷)

게시: bot_poster (KST 일요일 09:00 권장). 사용:
  python weekly_recap.py            # 생성 + 채널 게시
  python weekly_recap.py --no-post  # 출력만 (검증)
"""
import argparse
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from openai import OpenAI

import config
import db
import bot_poster
from top_signals import (
    load_texts, stock_universe, sector_universe,
    report_counts, rank_items, sample_messages,
)

KST = ZoneInfo("Asia/Seoul")
client = OpenAI(api_key=config.OPENAI_API_KEY)

WINDOW = 7   # 회고 윈도우(일)
TOP_N = 6    # 결산에 담을 종목 수 (게시물 길이 적정)


def consensus_week_change(conn, stock: str) -> dict | None:
    """지난 ~10일 내 컨센서스 스냅샷의 처음→마지막 목표가 평균·리포트 수 변화.
    히스토리가 1개뿐이면 None (비교 불가)."""
    rows = conn.execute(
        "SELECT consensus_generated_at, target_avg, report_count "
        "FROM ticker_consensus_history WHERE stock_name=%s "
        "AND consensus_generated_at >= NOW() - interval '10 days' "
        "ORDER BY consensus_generated_at",
        (stock,),
    ).fetchall()
    if len(rows) < 2:
        return None
    first, last = rows[0], rows[-1]
    o = float(first[1]) if first[1] is not None else None
    n = float(last[1]) if last[1] is not None else None
    pct = round((n - o) / o * 100, 1) if (o and n) else None
    added = None
    if last[2] is not None and first[2] is not None:
        added = int(last[2]) - int(first[2])
    return {"target_pct": pct, "report_added": added}


def build(conn) -> list[dict]:
    texts = load_texts(conn, WINDOW)
    sectors = sector_universe(conn)
    universe = stock_universe(conn, WINDOW, sectors)
    rep = report_counts(conn, WINDOW)
    top = rank_items(universe, texts, rep)[:TOP_N]
    out = []
    for it in top:
        out.append({
            **it,
            "change": consensus_week_change(conn, it["name"]),
            "samples": sample_messages(it["name"], texts),
        })
    return out


RECAP_PROMPT = """너는 한국 주식 시황 큐레이터다. 아래 JSON은 지난 한 주 이 채널이 가장 많이
포착한 종목들과, 텔레그램 회자수·리포트 수·그 사이 증권사 컨센서스(목표가 평균) 변화다.
'지난주 우리가 먼저·많이 짚은 종목, 그 후 어떻게 됐나'를 보여주는 주간 결산 코멘트를 작성하라.

JSON 응답:
{"intro": "한 주를 한 문장으로 (40자 이내)",
 "lines": {"종목명": "그 종목의 한 주 스토리 한 줄(45자 이내)", ...}}

규칙:
- 제공된 숫자(회자수/리포트/목표가변화)만 근거로. 없는 수치·주가·수익률 창작 절대 금지.
- 회자수가 높은데 리포트가 적었으면 '리포트보다 먼저 포착' 뉘앙스 가능.
- 목표가 평균이 올랐으면 '그 후 컨센서스 상향' 식으로 사실만.
- 과장·예측·매수권유 금지. 채널명·매체명 금지.
- 입력의 모든 종목을 포함."""


def narrate(items: list[dict]) -> dict:
    if not items:
        return {"intro": "", "lines": {}}
    payload = [{
        "종목": it["name"],
        "텔레그램_회자수": it["tele"],
        "리포트_수": it["report"],
        "목표가평균_변화pct": (it.get("change") or {}).get("target_pct"),
        "추가리포트": (it.get("change") or {}).get("report_added"),
        "텔레그램샘플": it["samples"],
    } for it in items]
    try:
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": RECAP_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
            ],
            max_completion_tokens=2000,  # gpt-5-nano 추론 토큰 여유
            reasoning_effort="low",
            response_format={"type": "json_object"},
        )
        data = json.loads((resp.choices[0].message.content or "").strip())
    except Exception:
        return {"intro": "", "lines": {}}
    return {"intro": str(data.get("intro", "")).strip(),
            "lines": data.get("lines", {}) if isinstance(data.get("lines"), dict) else {}}


def render(items: list[dict], nar: dict, start, end) -> str:
    out = [f"🏅 주간 결산 | 우리가 먼저 짚은 종목  ({start}~{end})", ""]
    if nar.get("intro"):
        out += [nar["intro"], ""]
    for i, it in enumerate(items, 1):
        out.append(f"{i}. {it['name']}  🗣️{it['tele']}회")
        sub = []
        if it["report"]:
            sub.append(f"리포트 {it['report']}건")
        ch = it.get("change") or {}
        if ch.get("target_pct") is not None:
            p = ch["target_pct"]
            arrow = "▲" if p > 0 else ("▼" if p < 0 else "—")
            sub.append(f"컨센서스 목표가 {arrow}{abs(p)}%")
        if sub:
            out.append("   • " + " · ".join(sub))
        ln = (nar.get("lines") or {}).get(it["name"])
        if ln:
            out.append(f"   └ {ln}")
        out.append("")
    out.append("🗣️ 한 주간 텔레그램 회자수 · 컨센서스=증권사 목표가 평균 변화")
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--no-post", action="store_true", help="채널 게시 생략, 출력만")
    args = p.parse_args()

    today = datetime.now(KST)
    start = (today - timedelta(days=WINDOW)).strftime("%m-%d")
    end = today.strftime("%m-%d")

    with db.connection() as conn:
        items = build(conn)
    if not items:
        print("회고할 종목 없음 — 게시 스킵")
        return

    nar = narrate(items)
    report = render(items, nar, start, end)
    print(report)
    if not args.no_post:
        bot_poster.post(report, kind="weekly_recap")
        print("\n[게시 완료]")


if __name__ == "__main__":
    main()
