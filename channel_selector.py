from telethon.tl.types import Channel
import db


def _channel_type(entity) -> str:
    if isinstance(entity, Channel):
        return "channel" if getattr(entity, "broadcast", False) else "supergroup"
    return "group"


def sync_channels(dialogs: list) -> tuple[int, int]:
    """현재 구독 중인 dialogs를 DB와 동기화.

    - dialogs에 있는 채널: upsert, selected=TRUE
    - dialogs에 없는데 DB에 있던 채널: selected=FALSE (구독 해제됨)

    반환: (selected 개수, 구독 해제된 개수)
    """
    rows = []
    for d in dialogs:
        e = d.entity
        rows.append((
            int(e.id),
            getattr(e, "username", None),
            d.name or getattr(e, "username", None) or str(e.id),
            _channel_type(e),
        ))
    current_ids = [r[0] for r in rows]

    with db.connection() as conn, conn.cursor() as cur:
        if rows:
            cur.executemany(
                """
                INSERT INTO channels (id, username, title, type, selected)
                VALUES (%s, %s, %s, %s, TRUE)
                ON CONFLICT (id) DO UPDATE
                  SET username   = EXCLUDED.username,
                      title      = EXCLUDED.title,
                      type       = EXCLUDED.type,
                      selected   = TRUE,
                      updated_at = NOW()
                """,
                rows,
            )
        cur.execute(
            "UPDATE channels SET selected = FALSE, updated_at = NOW() "
            "WHERE selected = TRUE AND NOT (id = ANY(%s)) RETURNING id",
            (current_ids,),
        )
        unsubscribed = cur.rowcount or 0

    return len(current_ids), unsubscribed
