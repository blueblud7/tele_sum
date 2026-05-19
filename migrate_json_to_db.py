"""1회성: last_seen.json + selected_channels.json + channels_dump.json → Neon Postgres.

사용:
    python migrate_json_to_db.py            # 적용
    python migrate_json_to_db.py --dry-run  # 미리보기만
"""
import argparse
import json
import os
import db


def _load_json(path: str):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _seed_channels_from_dump(conn, dump) -> int:
    """channels_dump.json: list of {id, name/title, username, type, ...}"""
    if not dump:
        return 0
    rows = []
    for item in dump:
        ch_id = item.get("id")
        if ch_id is None:
            continue
        title = item.get("name") or item.get("title") or item.get("username") or str(ch_id)
        username = item.get("username")
        ctype = item.get("type") or ("channel" if item.get("broadcast") else None)
        rows.append((int(ch_id), username, title, ctype))
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO channels (id, username, title, type)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
              SET username = COALESCE(EXCLUDED.username, channels.username),
                  title    = COALESCE(EXCLUDED.title, channels.title),
                  type     = COALESCE(EXCLUDED.type, channels.type),
                  updated_at = NOW()
            """,
            rows,
        )
    return len(rows)


def _ensure_channels(conn, ids: list[int]) -> None:
    """FK 충돌 방지용: 누락된 채널 id를 최소 정보로 채워 넣는다 (다음 실행에서 메타 갱신됨)."""
    if not ids:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO channels (id, title)
            VALUES (%s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            [(int(i), str(i)) for i in ids],
        )


def main(dry_run: bool) -> None:
    last_seen = _load_json("last_seen.json") or {}
    selected = _load_json("selected_channels.json") or []
    dump = _load_json("channels_dump.json")

    print(f"발견: last_seen {len(last_seen)}건, selected {len(selected)}개, dump {len(dump) if dump else 0}건")
    if dry_run:
        print("(dry-run) 변경하지 않고 종료.")
        return

    with db.connection() as conn:
        seeded = _seed_channels_from_dump(conn, dump)
        print(f"channels_dump → channels upsert: {seeded}건")

        all_ids = set(int(k) for k in last_seen.keys()) | set(int(i) for i in selected)
        _ensure_channels(conn, sorted(all_ids))

        # selected 마킹
        with conn.cursor() as cur:
            cur.execute("UPDATE channels SET selected = FALSE WHERE selected = TRUE")
            if selected:
                cur.execute(
                    "UPDATE channels SET selected = TRUE WHERE id = ANY(%s)",
                    ([int(i) for i in selected],),
                )
        print(f"selected 마킹: {len(selected)}개")

        # ingest_state 적재
        if last_seen:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO ingest_state (channel_id, last_seen_id, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (channel_id) DO UPDATE
                      SET last_seen_id = EXCLUDED.last_seen_id,
                          updated_at = NOW()
                    """,
                    [(int(k), int(v)) for k, v in last_seen.items()],
                )
            print(f"ingest_state 적재: {len(last_seen)}건")

    print("완료.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
