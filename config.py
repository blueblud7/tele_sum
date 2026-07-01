import os
from dotenv import load_dotenv

load_dotenv(override=True)

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", 0))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TARGET_CHANNEL = os.getenv("TARGET_CHANNEL", "")

# 모든 발송물 하단 서명 (공유·캡처 시 출처/구독 유도). 빈 문자열이면 푸터 미부착.
# @핸들일 때만 자동 기본값 생성, 숫자 chat_id면 기본 비활성(중복/노출 방지).
_default_footer = (
    f"━━━━━━━━━━\n📡 {TARGET_CHANNEL} · 100+ 채널·증권사 리포트 교차 신호"
    if TARGET_CHANNEL.startswith("@")
    else ""
)
CHANNEL_FOOTER = os.getenv("CHANNEL_FOOTER", _default_footer).strip()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")

LOOKBACK_MINUTES = int(os.getenv("LOOKBACK_MINUTES", 15))
MIN_MESSAGES = int(os.getenv("MIN_MESSAGES", 1))
LANGUAGE = os.getenv("LANGUAGE", "korean")
MAX_FORWARDS_PER_CHANNEL = int(os.getenv("MAX_FORWARDS_PER_CHANNEL", 3))

CHANNEL_FILTER = [
    ch.strip()
    for ch in os.getenv("CHANNEL_FILTER", "").split(",")
    if ch.strip()
]

DATABASE_URL = os.getenv("DATABASE_URL", "")
