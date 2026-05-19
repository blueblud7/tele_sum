import db


def load() -> dict[str, int]:
    """채널별 last_seen_id를 DB에서 로드. {channel_id_str: msg_id}"""
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT channel_id, last_seen_id FROM ingest_state"
        ).fetchall()
    return {str(ch_id): int(msg_id) for ch_id, msg_id in rows}


def save(state: dict[str, int]) -> None:
    """채널별 last_seen_id를 DB에 upsert."""
    if not state:
        return
    items = [(int(ch_id), int(msg_id)) for ch_id, msg_id in state.items()]
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO ingest_state (channel_id, last_seen_id, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (channel_id) DO UPDATE
                  SET last_seen_id = EXCLUDED.last_seen_id,
                      updated_at = NOW()
                """,
                items,
            )
