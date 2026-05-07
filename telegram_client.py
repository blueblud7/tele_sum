from datetime import datetime, timezone, timedelta
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat
import config

client = TelegramClient("tele_sum_session", config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH)


async def get_channels():
    """구독 중인 채널/그룹 목록 반환"""
    channels = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if not isinstance(entity, (Channel, Chat)):
            continue
        if config.CHANNEL_FILTER:
            username = getattr(entity, "username", None) or ""
            if username not in config.CHANNEL_FILTER and str(entity.id) not in config.CHANNEL_FILTER:
                continue
        channels.append(dialog)
    return channels


async def fetch_messages(dialog, hours_back: int) -> list[dict]:
    """채널의 최근 메시지 수집"""
    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    messages = []

    async for msg in client.iter_messages(dialog.entity, limit=500):
        if msg.date < since:
            break
        if not msg.text:
            continue
        messages.append({
            "date": msg.date.strftime("%Y-%m-%d %H:%M"),
            "text": msg.text[:1000],  # 메시지당 최대 1000자
        })

    return list(reversed(messages))


async def collect_all(hours_back: int) -> dict[str, list[dict]]:
    """모든 채널 메시지 수집"""
    result = {}
    channels = await get_channels()
    print(f"채널 {len(channels)}개 발견")

    for dialog in channels:
        name = dialog.name or dialog.entity.username or str(dialog.entity.id)
        messages = await fetch_messages(dialog, hours_back)
        if len(messages) >= config.MIN_MESSAGES:
            result[name] = messages
            print(f"  [{name}] {len(messages)}개 메시지 수집")
        else:
            print(f"  [{name}] 메시지 부족 ({len(messages)}개) - 스킵")

    return result
