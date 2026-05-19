"""selected=true 채널의 과거 메시지를 messages 테이블에 백필.

사용:
    python backfill.py                  # 기본 7일
    python backfill.py --days 30
    python backfill.py --days 7 --limit-per-channel 500
    python backfill.py --channel-ids 1928078035,1378197756

FloodWait 발생 시 자동 대기 (작은 값은 무시, 큰 값은 stderr에 표시).
ON CONFLICT DO NOTHING으로 idempotent — 여러 번 돌려도 안전.
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone, timedelta
from telethon.errors import FloodWaitError, ChannelPrivateError

import db
import telegram_client as tc


BATCH_SIZE = 500


async def _fetch_channel(client, entity, cutoff_date, limit: int | None):
    """채널의 최근 메시지를 cutoff까지 수집. 가장 최근부터 역순 iter."""
    out = []
    count = 0
    async for msg in client.iter_messages(entity, limit=limit):
        if msg.date < cutoff_date:
            break
        if not msg.text and not msg.media:
            continue
        out.append({
            "id": msg.id,
            "posted_at": msg.date,
            "text": (msg.text or "")[:4000],
            "has_media": bool(msg.media),
        })
        count += 1
    return out


def _insert_batch(rows: list[tuple]) -> int:
    if not rows:
        return 0
    with db.connection() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO messages
                (channel_id, message_id, posted_at, text, has_media, raw)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (channel_id, message_id) DO NOTHING
            """,
            rows,
        )
        return cur.rowcount or 0


def _load_targets(channel_ids: list[int] | None) -> list[tuple[int, str | None, str | None]]:
    with db.connection() as conn:
        if channel_ids:
            rows = conn.execute(
                "SELECT id, username, title FROM channels WHERE id = ANY(%s)",
                (channel_ids,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, username, title FROM channels WHERE selected = TRUE"
            ).fetchall()
    return [(int(r[0]), r[1], r[2]) for r in rows]


async def backfill(days: int, channel_ids: list[int] | None, limit_per_channel: int | None):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    targets = _load_targets(channel_ids)
    print(f"백필 대상: {len(targets)}개 채널, cutoff={cutoff.isoformat()}")

    import config
    await tc.client.start(phone=config.TELEGRAM_PHONE)

    total_inserted = 0
    total_fetched = 0
    for i, (ch_id, username, title) in enumerate(targets, 1):
        label = f"{title or username or ch_id}"
        try:
            entity = await tc.client.get_entity(ch_id)
        except (ChannelPrivateError, ValueError) as e:
            print(f"  [{i}/{len(targets)}] {label}: 접근 불가 ({type(e).__name__}) — 건너뜀")
            continue

        attempt_fetched = 0
        while True:
            try:
                msgs = await _fetch_channel(tc.client, entity, cutoff, limit_per_channel)
                attempt_fetched = len(msgs)
                break
            except FloodWaitError as e:
                wait_s = int(e.seconds) + 1
                if wait_s > 300:
                    print(f"  [{i}/{len(targets)}] {label}: FloodWait {wait_s}s — 건너뜀", file=sys.stderr)
                    msgs = []
                    break
                print(f"  [{i}/{len(targets)}] {label}: FloodWait {wait_s}s 대기...")
                await asyncio.sleep(wait_s)

        if not msgs:
            print(f"  [{i}/{len(targets)}] {label}: 0건")
            continue

        rows = [
            (
                ch_id,
                m["id"],
                m["posted_at"],
                m["text"] or None,
                m["has_media"],
                json.dumps({k: v for k, v in m.items() if k != "posted_at"},
                          ensure_ascii=False, default=str),
            )
            for m in msgs
        ]
        ch_inserted = 0
        for start in range(0, len(rows), BATCH_SIZE):
            ch_inserted += _insert_batch(rows[start:start + BATCH_SIZE])
        total_fetched += attempt_fetched
        total_inserted += ch_inserted
        print(f"  [{i}/{len(targets)}] {label}: fetched={attempt_fetched}, inserted={ch_inserted}")

    await tc.client.disconnect()
    print(f"\n완료. fetched={total_fetched}, inserted={total_inserted} (중복은 무시됨)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit-per-channel", type=int, default=None,
                        help="채널당 최대 메시지 수 (안전장치)")
    parser.add_argument("--channel-ids", type=str, default=None,
                        help="쉼표 구분 channel id, 미지정 시 selected=true 전체")
    args = parser.parse_args()

    ids = None
    if args.channel_ids:
        ids = [int(x.strip()) for x in args.channel_ids.split(",") if x.strip()]

    asyncio.run(backfill(args.days, ids, args.limit_per_channel))


if __name__ == "__main__":
    main()
