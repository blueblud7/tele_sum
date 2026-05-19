import asyncio
import json
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat
import config


async def main():
    client = TelegramClient("tele_sum_session", config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH)
    await client.connect()

    items = []
    async for d in client.iter_dialogs():
        e = d.entity
        if not isinstance(e, (Channel, Chat)):
            continue
        is_broadcast = bool(getattr(e, "broadcast", False))
        items.append({
            "id": e.id,
            "name": d.name,
            "type": "채널" if is_broadcast else "그룹",
            "username": getattr(e, "username", None),
        })

    with open("channels_dump.json", "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    for i, c in enumerate(items, 1):
        u = f"  @{c['username']}" if c["username"] else ""
        print(f"{i:3}. [{c['type']}] {c['name']}{u}")

    print(f"\nTOTAL {len(items)}")
    await client.disconnect()


asyncio.run(main())
