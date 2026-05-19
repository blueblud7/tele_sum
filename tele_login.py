import asyncio
import os
import sys
from pathlib import Path
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
import config

HASH_FILE = Path(".phone_code_hash")


async def main():
    client = TelegramClient("tele_sum_session", config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH)
    await client.connect()

    if await client.is_user_authorized():
        print("ALREADY_AUTHORIZED")
        await client.disconnect()
        return

    code = os.getenv("TG_CODE", "").strip()
    password = os.getenv("TG_PASSWORD", "").strip()

    if not code:
        sent = await client.send_code_request(config.TELEGRAM_PHONE)
        HASH_FILE.write_text(sent.phone_code_hash)
        print(f"CODE_SENT to {config.TELEGRAM_PHONE}")
        await client.disconnect()
        return

    if not HASH_FILE.exists():
        print("ERROR: no phone_code_hash file. Run without TG_CODE first.")
        await client.disconnect()
        sys.exit(1)

    phone_code_hash = HASH_FILE.read_text().strip()

    try:
        await client.sign_in(
            phone=config.TELEGRAM_PHONE,
            code=code,
            phone_code_hash=phone_code_hash,
        )
        print("LOGGED_IN")
        HASH_FILE.unlink(missing_ok=True)
    except SessionPasswordNeededError:
        if not password:
            print("PASSWORD_NEEDED")
            await client.disconnect()
            sys.exit(2)
        await client.sign_in(password=password)
        print("LOGGED_IN_2FA")
        HASH_FILE.unlink(missing_ok=True)

    await client.disconnect()


asyncio.run(main())
