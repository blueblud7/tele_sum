"""텔레그램 파생 신호 사전계산 → Supabase 적재.

reportportal 대시보드가 매 요청마다 raw 메시지 1000건을 받아 인메모리로 계산하던
②종목 회자수 · ③테마 · ④총량을, 여기서 미리 계산해 작은 테이블에 저장한다.
reportportal은 이 테이블만 읽으면 된다.

매칭/토큰화 로직은 reportportal/web/lib/dashboard.ts 를 그대로 포팅.

사용:
    python tele_signals.py                 # 기본 윈도우 [2,7,14,30]일
    python tele_signals.py --windows 7,14
"""
import argparse
import re
from collections import Counter

import db

WINDOWS = [2, 7, 14, 30]

# ── dashboard.ts SECTOR_LIKE 포팅 ──
SECTOR_LIKE = {
    "기타", "전체", "시황", "건설", "반도체", "게임", "은행", "음식료", "석유화학",
    "철강금속", "제약", "에너지", "자동차", "지주회사", "유틸리티", "화학", "운송",
    "운수", "조선", "디스플레이", "통신", "유통", "엔터", "여행", "면세점", "호텔",
}

# ── dashboard.ts STOP 포팅 ──
STOP = {
    "리뷰", "review", "발행", "보고서", "분석", "전망", "시황", "코멘트", "업데이트",
    "투자의견", "목표주가", "현재가", "주가", "실적", "기업", "산업", "리포트",
    "종목", "이슈", "이번", "지난", "오늘", "내일", "올해", "작년", "이상", "이하",
    "발표", "관련", "원문", "출처", "기사", "https", "http", "com", "co", "kr",
    "naver", "yahoo", "finance", "policy", "articles", "status", "ipo",
    "weekly", "daily", "monthly", "outlook", "preview",
    "분기", "월간", "주간", "상반기", "하반기", "연간", "확대", "축소",
    "유지", "목표가", "또는", "이내", "명사형", "핵심", "포인트", "결론", "한줄",
    "공백", "포함", "정확히", "리스크", "시사점", "유지·목표",
    "상승", "하락", "증가", "감소", "개선", "악화", "전년동기", "전분기",
    "각각", "각", "내외",
    "1q", "2q", "3q", "4q", "1q25", "2q25", "3q25", "4q25", "1q26", "2q26", "3q26", "4q26",
    "fy25", "fy26", "2h25", "2h26", "1h25", "1h26", "2025", "2026",
    "the", "a", "an", "of", "and", "for", "to", "in", "on", "at", "is", "are",
    "as", "by", "vs", "with", "from", "into", "this", "that", "these", "those",
    "be", "or", "it", "its", "has", "have", "will", "can", "not", "no", "us", "uk", "eu",
    # tele 본문 보일러플레이트(서술어미·공지·면책)
    "있습니다", "있으며", "있는", "있다", "없습니다", "합니다", "됩니다", "입니다",
    "습니다", "바랍니다", "같습니다", "드립니다", "보입니다", "됩니다만", "한다",
    "현재", "무단", "전재", "재배포", "저작권", "구독", "채널", "클릭", "문의",
    "뉴스", "기자", "속보", "공유", "텔레그램", "참고", "주의",
    "무단전재", "금지", "제목", "대한", "종합", "위해", "따라", "것으로", "관련주",
    # 매체명
    "이데일리", "연합뉴스", "머니투데이", "한국경제", "서울경제", "매일경제",
    "파이낸셜뉴스", "헤럴드경제", "아시아경제", "이투데이",
}

URL_RE = re.compile(r"https?://\S+")
TOKEN_RE = re.compile(r"[A-Za-z]{2,}|[가-힣]{2,}")
SUFFIXES = ["홀딩스", "지주", "그룹", "건설", "전자", "케미칼", "에너지머티리얼즈",
            "에너지", "증권", "생명", "손해보험", "쇼핑", "산업", "테크"]


def _is_ascii(s: str) -> bool:
    return all(ord(ch) < 128 for ch in s)


def stock_aliases(name: str) -> list[tuple[str, bool]]:
    """dashboard.ts stockAliases 포팅. (alias, needs_boundary) 목록."""
    if not name:
        return []
    out: list[tuple[str, bool]] = []
    seen: set[str] = set()

    def add(a: str):
        t = a.strip()
        if not t or len(t) < 2 or t in seen:
            return
        seen.add(t)
        needs_boundary = _is_ascii(t) or len(t) <= 2
        out.append((t, needs_boundary))

    add(name)
    cleaned = re.sub(r"\(주\)|주식회사", "", name).strip()
    if cleaned and cleaned != name:
        add(cleaned)
    for suf in SUFFIXES:
        if cleaned.endswith(suf) and len(cleaned) - len(suf) >= 2:
            short = cleaned[: -len(suf)]
            if len(short) >= 2:
                add(short)
    return out


def count_matches(aliases: list[tuple[str, bool]], texts: list[str]) -> int:
    """dashboard.ts countMatches 포팅. 별칭이 등장한 '메시지 수'를 반환(언급 총량 아님)."""
    if not aliases or not texts:
        return 0
    bounded = [a for a, b in aliases if b]
    plain = [a for a, b in aliases if not b]
    bounded_re = None
    if bounded:
        joined = "|".join(re.escape(a) for a in bounded)
        bounded_re = re.compile(rf"(?<![A-Za-z0-9])(?:{joined})(?![A-Za-z0-9])")
    hits = 0
    for t in texts:
        if not t:
            continue
        ok = any(a in t for a in plain)
        if not ok and bounded_re is not None and bounded_re.search(t):
            ok = True
        if ok:
            hits += 1
    return hits


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    cleaned = URL_RE.sub(" ", text)
    return [w for w in TOKEN_RE.findall(cleaned) if w.lower() not in STOP]


def load_stock_universe(conn, days: int) -> list[str]:
    """sent_reports에서 최근 days일 distinct stock_name (섹터/기타 제외)."""
    rows = conn.execute(
        """
        SELECT DISTINCT stock_name FROM sent_reports
        WHERE report_date >= (NOW()::date - %s) AND stock_name IS NOT NULL
        """,
        (days,),
    ).fetchall()
    names = []
    for (n,) in rows:
        n = (n or "").strip()
        if n and n not in SECTOR_LIKE:
            names.append(n)
    return names


def load_tele_texts(conn, days: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT text FROM messages
        WHERE posted_at >= NOW() - make_interval(days => %s) AND text IS NOT NULL
        """,
        (days,),
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def compute_window(conn, days: int):
    texts = load_tele_texts(conn, days)
    stocks = load_stock_universe(conn, max(days, 14))  # 종목 유니버스는 최소 14일치
    # ② 종목 회자수
    mentions = []
    for name in stocks:
        cnt = count_matches(stock_aliases(name), texts)
        if cnt > 0:
            mentions.append((name, days, cnt))
    # ③ 테마 (tele 본문 첫 3줄 토큰화, 종목명 제외, 상위 30)
    lower_stocks = {s.lower() for s in stocks}
    c = Counter()
    for t in texts:
        head = " ".join(t.split("\n")[:3])
        for w in tokenize(head):
            c[w] += 1
    themes = []
    rank = 0
    for word, score in c.most_common():
        if word.lower() in lower_stocks:
            continue
        rank += 1
        themes.append((days, word, score, rank))
        if rank >= 30:
            break
    # ④ 총량
    kpi = (days, len(texts))
    return mentions, themes, kpi


def save_window(conn, days: int, mentions, themes, kpi):
    with conn.cursor() as cur:
        # 멘션: 이 윈도우 기존 행 삭제 후 재삽입(사라진 종목 정리)
        cur.execute("DELETE FROM tele_stock_mentions WHERE window_days = %s", (days,))
        if mentions:
            cur.executemany(
                """INSERT INTO tele_stock_mentions (stock_name, window_days, mention_count, computed_at)
                   VALUES (%s, %s, %s, NOW())""",
                mentions,
            )
        cur.execute("DELETE FROM tele_themes WHERE window_days = %s", (days,))
        if themes:
            cur.executemany(
                """INSERT INTO tele_themes (window_days, word, score, rank, computed_at)
                   VALUES (%s, %s, %s, %s, NOW())""",
                themes,
            )
        cur.execute(
            """INSERT INTO tele_kpi (window_days, msg_count, computed_at)
               VALUES (%s, %s, NOW())
               ON CONFLICT (window_days) DO UPDATE
                 SET msg_count = EXCLUDED.msg_count, computed_at = NOW()""",
            kpi,
        )


def run(windows: list[int]):
    with db.connection() as conn:
        for days in windows:
            mentions, themes, kpi = compute_window(conn, days)
            save_window(conn, days, mentions, themes, kpi)
            print(f"  [{days}d] 종목 {len(mentions)}개 회자, 테마 {len(themes)}개, 메시지 {kpi[1]}건")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--windows", default=",".join(map(str, WINDOWS)),
                   help="쉼표구분 일수, 기본 2,7,14,30")
    args = p.parse_args()
    windows = [int(x) for x in args.windows.split(",") if x.strip()]
    print(f"신호 사전계산 시작: windows={windows}")
    run(windows)
    db.close()
    print("완료.")


if __name__ == "__main__":
    main()
