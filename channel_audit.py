"""채널 가치/노이즈 진단 + 티어 분류 + selected 플래그 적용.

지표(최근 N일):
  per_day   하루 평균 건수 (볼륨/노이즈량)
  avg_len   평균 본문 길이 (충실도)
  low_sub%  빈/초단문(25자 미만) 비율 (저품질)
  sig%      종목 유니버스(sent_reports) 1개 이상 언급한 메시지 비율 (시그널 밀도)
  dup%      동일 본문이 다른 채널에도 있는 비율 (재게시/중복 = 독립가치 낮음)

티어:
  KEEP   고가치(시그널 밀도 높거나 1차 리서치) — 유지
  WATCH  중간 — 보류
  DROP   매크로/뉴스 피드(저시그널 대용량) 또는 중복·저품질 — 구독 해제 후보

사용:
  python channel_audit.py              # 진단·티어 표만 출력 (DB 변경 없음)
  python channel_audit.py --days 14    # 윈도우 변경
  python channel_audit.py --apply      # DROP 티어를 channels.selected=false 로 반영
"""
import argparse, re, hashlib
from collections import defaultdict
import db
from tele_signals import stock_aliases, load_stock_universe, URL_RE

# 매크로/뉴스 피드: 종목 시그널 낮은 대용량 피드 → 드롭 (사용자 결정)
MACRO_DROP_IDS = {
    2595233360,  # 해외정보 분석 (국제정치/지정학)
    1263412188,  # Market News Feed
    2583177558,  # Quick Financial News (@quick_engNews)
    2025567712,  # Quick Financial News (@usfinancialnews)
    1223459481,  # The Wall Street Journal World News
    1462338131,  # Donald J. Trump
    2506876270,  # 나박 AI 외신 속보
}
# 시그널 밀도 매우 높은 1차 리서치는 중복/저품질이어도 보호
PROTECT_SIG = 70

# 사용자 수동 KEEP 예외: 자동 분류상 DROP이지만 유지하기로 결정한 채널
#  - 1차 리서치 원본(중복으로 잡혔으나 원본 가능성): 키움 리서치센터, 키움 전략/시황 한지영
#  - 사용자 판단 유지: 선진짱 주식공부방, IT의 신 이형수, 이그전
KEEP_OVERRIDE_IDS = {
    1127984656,  # 키움증권 리서치센터
    1304649917,  # 키움증권 전략/시황 한지영
    1378197756,  # 선진짱 주식공부방
    1940491138,  # IT의 신 이형수
    1269723965,  # 이그전 (이은택의 그림 전략)
}


def analyze(days):
    with db.connection() as c:
        chans = {r[0]: (r[1] or "", r[2] or "") for r in c.execute(
            "select id, username, title from channels").fetchall()}
        rows = c.execute(
            "select channel_id, text from messages "
            "where posted_at >= now() - (%s||' days')::interval", (days,)).fetchall()
        universe = load_stock_universe(c, days)

    plain, bounded = [], []
    for nm in universe:
        for a, b in stock_aliases(nm):
            (bounded if b else plain).append(a)
    plain_re = re.compile("|".join(re.escape(a) for a in sorted(set(plain), key=len, reverse=True))) if plain else None
    bset = sorted(set(bounded))
    bounded_re = re.compile(rf"(?<![A-Za-z0-9])(?:{'|'.join(re.escape(a) for a in bset)})(?![A-Za-z0-9])") if bset else None

    def has_stock(t):
        return bool(t and ((plain_re and plain_re.search(t)) or (bounded_re and bounded_re.search(t))))

    def norm(t):
        if not t: return ""
        return re.sub(r"\s+", " ", URL_RE.sub(" ", t).lower()).strip()[:160]

    per = defaultdict(lambda: dict(n=0, low=0, lensum=0, sig=0, hashes=[]))
    owners = defaultdict(set)
    for ch, txt in rows:
        d = per[ch]; d["n"] += 1; d["lensum"] += len(txt or "")
        if len(txt or "") < 25: d["low"] += 1
        if has_stock(txt): d["sig"] += 1
        h = norm(txt)
        if h:
            hh = hashlib.md5(h.encode()).hexdigest()
            d["hashes"].append(hh); owners[hh].add(ch)
    cross = {h for h, o in owners.items() if len(o) >= 2}

    out = []
    for ch, d in per.items():
        n = d["n"]
        uname, title = chans.get(ch, ("", str(ch)))
        m = dict(ch=ch, uname=uname, title=title, n=n, per_day=round(n/days, 1),
                 avg_len=round(d["lensum"]/n), low=round(100*d["low"]/n),
                 sig=round(100*d["sig"]/n),
                 dup=round(100*sum(1 for h in d["hashes"] if h in cross)/n))
        out.append(m)
    return out


def tier(m):
    if m["ch"] in KEEP_OVERRIDE_IDS:
        return "KEEP", "수동 유지(사용자 결정)"
    if m["ch"] in MACRO_DROP_IDS:
        return "DROP", "매크로/뉴스(저시그널 대용량)"
    if m["sig"] >= PROTECT_SIG:
        return "KEEP", "1차 리서치(시그널 밀도 최상)"
    if m["dup"] >= 40 or m["low"] >= 50:
        why = []
        if m["dup"] >= 40: why.append(f"중복{m['dup']}%")
        if m["low"] >= 50: why.append(f"저품질{m['low']}%")
        return "DROP", "·".join(why)
    if m["sig"] >= 25 and m["dup"] < 25 and m["low"] < 30:
        return "KEEP", f"시그널{m['sig']}%"
    return "WATCH", f"시그널{m['sig']}% 중복{m['dup']}% 저품질{m['low']}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--apply", action="store_true", help="DROP 티어를 selected=false 로 반영")
    a = ap.parse_args()

    data = analyze(a.days)
    for m in data:
        m["tier"], m["why"] = tier(m)

    order = {"KEEP": 0, "WATCH": 1, "DROP": 2}
    data.sort(key=lambda m: (order[m["tier"]], -m["n"]))
    print(f"# 윈도우 {a.days}일 / 채널 {len(data)}개\n")
    cur = None
    for m in data:
        if m["tier"] != cur:
            cur = m["tier"]
            cnt = sum(1 for x in data if x["tier"] == cur)
            vol = sum(x["n"] for x in data if x["tier"] == cur)
            print(f"\n## {cur} ({cnt}개, {vol:,}건)")
            print(f"{'채널':28} {'/일':>6} {'길이':>5} {'시그널':>5} {'중복':>4} {'저품질':>5}  사유")
            print("-"*80)
        nm = m["title"][:26] or m["uname"]
        print(f"{nm:28.28} {m['per_day']:>6} {m['avg_len']:>5} {m['sig']:>4}% {m['dup']:>3}% {m['low']:>4}%  {m['why']}")

    drops = [m for m in data if m["tier"] == "DROP"]
    if a.apply:
        # 윈도우 내 DROP 티어 + 매크로 드롭 ID(최근 무활동이라도) − KEEP 예외
        ids = list(({m["ch"] for m in drops} | MACRO_DROP_IDS) - KEEP_OVERRIDE_IDS)
        with db.connection() as c:
            c.execute("update channels set selected=false, updated_at=now() where id = any(%s)", (ids,))
        print(f"\n[적용 완료] {len(ids)}개 채널 selected=false")
    else:
        print(f"\n적용하려면: python channel_audit.py --days {a.days} --apply  (DROP {len(drops)}개 구독 해제)")
    db.close()


if __name__ == "__main__":
    main()
