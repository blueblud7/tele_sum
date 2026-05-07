import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", 0))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE", "")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-nano")

HOURS_BACK = int(os.getenv("HOURS_BACK", 24))
MIN_MESSAGES = int(os.getenv("MIN_MESSAGES", 3))
LANGUAGE = os.getenv("LANGUAGE", "korean")

CHANNEL_FILTER = [
    ch.strip()
    for ch in os.getenv("CHANNEL_FILTER", "").split(",")
    if ch.strip()
]
