import os
from dotenv import load_dotenv

load_dotenv(override=True)

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", 0))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TARGET_CHANNEL = os.getenv("TARGET_CHANNEL", "")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-nano")

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
