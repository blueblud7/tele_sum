"""채널 성장 지표 수집기.

TARGET_CHANNEL(@financialnewssummary)의
  1) 구독자 수  → tele_channel_metrics (일 1회 스냅샷)
  2) 최근 게시물별 조회수/공유수 → tele_post_metrics (일 1회 스냅샷)
를 Supabase에 적재한다.

"무엇이 구독을 늘리는가"를 데이터로 보기 위한 기준선.
- 구독자 수: 어떤 게시물/날짜 이후 늘고 주는지
- forwards(공유수): 어떤 포맷이 실제로 공유되는지 = 콘텐츠 실험의 핵심 지표

기존 Telethon 사용자 세션(tele_sum_session)을 재사용한다.
봇 API가 아닌 사용자 클라이언트라야 게시물 .views / .forwards 를 읽을 수 있다.

KST 일 1회 크론 실행 권장 (run_metrics.sh).
"""
import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from telethon.tl.functions.channels import GetFullChannelRequest

import config
import db
import telegram_client as tc

# 게시물 지표를 읽어올 최근 게시물 수 (하루 4회 다이제스트 + TOP10 → 넉넉히)
POST_LIMIT = 60


def _kst_today():
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


async def collect() -> dict:
    await tc.client.start(phone=config.TELEGRAM_PHONE)
    try:
        entity = await tc.client.get_entity(config.TARGET_CHANNEL)

        full = await tc.client(GetFullChannelRequest(entity))
        subscribers = full.full_chat.participants_count

        posts = []
        async for msg in tc.client.iter_messages(entity, limit=POST_LIMIT):
            if msg.action is not None:  # 가입/핀 등 서비스 메시지 제외
                continue
            posts.append({
                "message_id": msg.id,
                "posted_at": msg.date,
                "views": getattr(msg, "views", None),
                "forwards": getattr(msg, "forwards", None),
                "text_preview": (msg.text or "")[:120],
            })
        return {"subscribers": subscribers, "posts": posts}
    finally:
        await tc.client.disconnect()


def save(data: dict) -> None:
    today = _kst_today()
    subs = data["subscribers"]
    posts = data["posts"]

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tele_channel_metrics (snapshot_date, subscribers, snapshot_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (snapshot_date)
            DO UPDATE SET subscribers = EXCLUDED.subscribers,
                          snapshot_at = EXCLUDED.snapshot_at
            """,
            (today, subs, datetime.now(timezone.utc)),
        )

        if posts:
            cur.executemany(
                """
                INSERT INTO tele_post_metrics
                    (message_id, snapshot_date, posted_at, views, forwards, text_preview)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (message_id, snapshot_date)
                DO UPDATE SET views = EXCLUDED.views,
                              forwards = EXCLUDED.forwards,
                              posted_at = EXCLUDED.posted_at,
                              text_preview = EXCLUDED.text_preview
                """,
                [
                    (
                        p["message_id"], today, p["posted_at"],
                        p["views"], p["forwards"], p["text_preview"],
                    )
                    for p in posts
                ],
            )


def main():
    data = asyncio.run(collect())
    save(data)

    subs = data["subscribers"]
    posts = data["posts"]
    top = sorted(
        (p for p in posts if p["forwards"]),
        key=lambda p: p["forwards"],
        reverse=True,
    )[:5]
    print(f"구독자: {subs}명 · 게시물 {len(posts)}건 적재")
    if top:
        print("공유 TOP:")
        for p in top:
            print(f"  🔁{p['forwards']} 👁{p['views']}  {p['text_preview'][:50]}")


if __name__ == "__main__":
    main()
