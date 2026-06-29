"""빠진 구간의 요약(summaries)을 백필된 messages로부터 재생성해 DB에 채운다.

- 기간을 KST 하루 단위로 끊어, 각 날짜의 메시지로 채널별 요약 + 통합 다이제스트를 생성.
- summaries 테이블에 kind='channel' / kind='digest'로 저장. meta.backfilled=true 로 표시.
- 공개 채널 게시는 하지 않는다 (DB만 채움).
- 토큰 초과 방지: 채널당 메시지 수 상한(--max-msgs).

사용:
    python backfill_summaries.py --start 2026-06-19 --end 2026-06-24
    python backfill_summaries.py --start 2026-06-19 --end 2026-06-24 --max-msgs 120 --dry-run
"""
import argparse
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import db
import repo
import summarizer
import config

KST = ZoneInfo("Asia/Seoul")


def load_channel_data(ws, we, max_msgs: int):
    """[ws, we) UTC 구간의 메시지를 채널별로 로드. 채널당 max_msgs(최신순)로 제한."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.title, c.username, m.message_id, m.posted_at, m.text, m.has_media
            FROM messages m
            JOIN channels c ON c.id = m.channel_id
            WHERE m.posted_at >= %s AND m.posted_at < %s
              AND m.text IS NOT NULL AND m.text <> ''
            ORDER BY c.id, m.posted_at
            """,
            (ws, we),
        )
        rows = cur.fetchall()

    data: dict[str, list[dict]] = {}
    name_to_id: dict[str, int] = {}
    for cid, title, username, mid, posted_at, text, has_media in rows:
        name = title or username or str(cid)
        name_to_id[name] = int(cid)
        data.setdefault(name, []).append({
            "id": int(mid),
            "date": posted_at.astimezone(KST).strftime("%Y-%m-%d %H:%M"),
            "text": text or "",
            "has_media": bool(has_media),
        })
    # 채널당 상한 (최신 max_msgs건만)
    for name in data:
        if len(data[name]) > max_msgs:
            data[name] = data[name][-max_msgs:]
    return data, name_to_id


def build_link_catalog(results: dict) -> list[tuple[str, str]]:
    out, seen = [], set()
    for r in results.values():
        for link in r.get("links", []):
            url = (link.get("url") or "").strip()
            title = (link.get("title") or "").strip() or "(제목 없음)"
            if url and url not in seen:
                seen.add(url)
                out.append((title, url))
    return out[:40]


def process_day(day_kst, max_msgs: int, dry_run: bool):
    ws = datetime(day_kst.year, day_kst.month, day_kst.day, tzinfo=KST)
    we = ws + timedelta(days=1)
    ws_utc, we_utc = ws.astimezone(ZoneInfo("UTC")), we.astimezone(ZoneInfo("UTC"))

    data, name_to_id = load_channel_data(ws_utc, we_utc, max_msgs)
    n_msgs = sum(len(v) for v in data.values())
    print(f"\n=== {day_kst.isoformat()} (KST) === 채널 {len(data)}개, 메시지 {n_msgs}건")
    if not data:
        print("  (메시지 없음 — 건너뜀)")
        return

    results = summarizer.summarize_all(data)
    link_catalog = build_link_catalog(results)

    if dry_run:
        topics = summarizer.aggregate_digest(results, link_catalog)
        if topics:
            topics = summarizer.review_digest(topics, link_catalog)
        digest = summarizer.render_digest(topics)
        print(f"  [dry-run] digest {len(digest)}자, 채널요약 "
              f"{sum(1 for r in results.values() if r.get('summary','').strip())}개 (저장 안 함)")
        return

    # 채널별 요약 저장
    n_ch = 0
    for name, r in results.items():
        s = (r.get("summary") or "").strip()
        if not s:
            continue
        repo.save_summary(
            kind="channel",
            channel_id=name_to_id.get(name),
            content=s,
            period_start=ws_utc,
            period_end=we_utc,
            model=config.OPENAI_MODEL,
            meta={"backfilled": True, "important_ids": r.get("important_ids", []),
                  "links": r.get("links", [])},
        )
        n_ch += 1

    # 통합 다이제스트 저장
    topics = summarizer.aggregate_digest(results, link_catalog)
    if topics:
        topics = summarizer.review_digest(topics, link_catalog)
    digest = summarizer.render_digest(topics)
    if digest:
        repo.save_summary(
            kind="digest",
            content=digest,
            period_start=ws_utc,
            period_end=we_utc,
            model=config.OPENAI_MODEL,
            meta={"backfilled": True, "channels": list(data.keys()), "topics": topics},
        )
    print(f"  저장: 채널요약 {n_ch}개, digest {'O' if digest else 'X'} ({len(digest)}자)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="시작일 KST YYYY-MM-DD (포함)")
    p.add_argument("--end", required=True, help="종료일 KST YYYY-MM-DD (미포함)")
    p.add_argument("--max-msgs", type=int, default=120, help="채널당 메시지 상한")
    p.add_argument("--dry-run", action="store_true", help="생성만 하고 저장 안 함")
    args = p.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    d = start
    while d < end:
        process_day(d, args.max_msgs, args.dry_run)
        d += timedelta(days=1)

    db.close()
    print("\n완료.")


if __name__ == "__main__":
    main()
