from datetime import datetime, timezone, timedelta
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat
import config

client = TelegramClient("tele_sum_session", config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH)


async def get_all_channels() -> list:
    channels = []
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if isinstance(entity, (Channel, Chat)):
            channels.append(dialog)
    return channels


async def fetch_new_messages(dialog, last_seen_id: int | None, lookback_minutes: int) -> list[dict]:
    """
    last_seen_id 이후 새 메시지 수집. 첫 실행이면 lookback_minutes 윈도우 사용.
    msg.id, date, text, has_media 포함.
    """
    if last_seen_id is None:
        since = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    else:
        since = None

    messages = []
    async for msg in client.iter_messages(dialog.entity, limit=200):
        if last_seen_id is not None:
            if msg.id <= last_seen_id:
                break
        else:
            if msg.date < since:
                break
        if not msg.text and not msg.media:
            continue
        messages.append({
            "id": msg.id,
            "date": msg.date.strftime("%Y-%m-%d %H:%M"),
            "posted_at": msg.date,
            "text": (msg.text or "")[:1000],
            "has_media": bool(msg.media),
        })

    return list(reversed(messages))


async def collect_new(dialogs: list, last_seen: dict[str, int], lookback_minutes: int):
    """
    각 채널의 새 메시지 수집. (channel_data, max_id_per_channel) 반환.
    channel_data: {name: [msgs]}
    max_ids: {channel_id_str: max_msg_id}
    dialog_by_name: {name: dialog} (포워딩에 사용)
    """
    channel_data, max_ids, dialog_by_name, all_msgs = {}, {}, {}, {}
    for dialog in dialogs:
        name = dialog.name or getattr(dialog.entity, "username", None) or str(dialog.entity.id)
        ch_id = str(dialog.entity.id)
        last_id = last_seen.get(ch_id)
        msgs = await fetch_new_messages(dialog, last_id, lookback_minutes)
        if not msgs:
            continue
        max_ids[ch_id] = max(m["id"] for m in msgs)
        all_msgs[int(dialog.entity.id)] = msgs
        if len(msgs) >= config.MIN_MESSAGES:
            channel_data[name] = msgs
            dialog_by_name[name] = dialog
            print(f"  [{name}] {len(msgs)}개 신규")
        else:
            print(f"  [{name}] {len(msgs)}개 (min 미달, 커서만 갱신)")
    return channel_data, max_ids, dialog_by_name, all_msgs


def message_link(dialog, msg_id: int) -> str:
    """채널 메시지에 대한 t.me 링크 생성 (공개 채널은 username, 비공개는 c/<id>)."""
    entity = dialog.entity
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"
    eid = abs(int(getattr(entity, "id", 0)))
    s = str(eid)
    if s.startswith("100"):
        s = s[3:]
    return f"https://t.me/c/{s}/{msg_id}"
