import json
from datetime import datetime, timezone
import db


def save_messages(by_channel: dict[int, list[dict]]) -> int:
    """채널별 메시지 목록을 messages 테이블에 upsert. 저장된 행 수 반환."""
    rows = []
    for ch_id, msgs in by_channel.items():
        for m in msgs:
            rows.append((
                int(ch_id),
                int(m["id"]),
                m["posted_at"],
                m.get("text") or None,
                bool(m.get("has_media", False)),
                json.dumps(
                    {k: v for k, v in m.items() if k != "posted_at"},
                    ensure_ascii=False,
                    default=str,
                ),
            ))
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
    return len(rows)


def save_summary(
    *,
    kind: str,
    content: str,
    period_start: datetime,
    period_end: datetime,
    channel_id: int | None = None,
    model: str | None = None,
    meta: dict | None = None,
) -> None:
    if not content.strip():
        return
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO summaries
                (channel_id, kind, period_start, period_end, content, model, meta)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                channel_id,
                kind,
                period_start,
                period_end,
                content,
                model,
                json.dumps(meta or {}, ensure_ascii=False, default=str),
            ),
        )


def save_sent_post(
    *,
    kind: str,
    text: str,
    message_ids: list[int],
    chat: str | None = None,
    pinned: bool = False,
    meta: dict | None = None,
) -> int | None:
    """실제로 발송된 게시물을 tele_sent_posts에 기록. 새 행 id 반환.
    message_ids가 비면(=발송 실패) 저장하지 않고 None 반환."""
    ids = [int(m) for m in message_ids if m is not None]
    if not ids:
        return None
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tele_sent_posts (kind, text, message_ids, chat, pinned, meta)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (
                kind,
                text,
                ids,
                chat,
                pinned,
                json.dumps(meta or {}, ensure_ascii=False, default=str),
            ),
        )
        return cur.fetchone()[0]


def mark_pinned(message_id: int) -> None:
    """해당 message_id를 포함한 발송 기록을 pinned=TRUE로 표시 (best-effort)."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tele_sent_posts SET pinned = TRUE WHERE %s = ANY(message_ids)",
            (int(message_id),),
        )


def message_window(msgs: list[dict]) -> tuple[datetime, datetime]:
    """메시지 목록의 [최소 posted_at, 최대 posted_at] 반환. 비어 있으면 (now, now)."""
    if not msgs:
        now = datetime.now(timezone.utc)
        return now, now
    dates = [m["posted_at"] for m in msgs]
    return min(dates), max(dates)
