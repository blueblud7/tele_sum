import time
import requests
import config

API_BASE = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
MAX_LEN = 3500
RETRIES = 4


def _chunk(text: str, limit: int = MAX_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit:
            if current:
                chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks


def _send_one(chunk: str) -> None:
    """타임아웃은 재시도하지 않음 (서버가 이미 받았을 수 있어 중복 위험).
    연결 오류만 재시도."""
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.post(
                f"{API_BASE}/sendMessage",
                data={
                    "chat_id": config.TARGET_CHANNEL,
                    "text": chunk,
                    "disable_web_page_preview": "true",
                },
                timeout=(10, 90),  # (connect, read)
            )
            if r.ok and r.json().get("ok"):
                return
            last_err = f"{r.status_code} {r.text}"
            # API 거부는 재시도 무의미
            return _raise(last_err)
        except requests.exceptions.ReadTimeout as e:
            # 서버가 이미 받았을 수 있음 → 재시도 금지
            raise RuntimeError(f"Bot post read-timeout (메시지 전달 여부 불확실): {e}")
        except (requests.exceptions.ConnectionError, requests.exceptions.ConnectTimeout) as e:
            last_err = str(e)
        if attempt < RETRIES:
            time.sleep(2 * attempt)
    raise RuntimeError(f"Bot post failed after {RETRIES} attempts: {last_err}")


def _raise(msg: str):
    raise RuntimeError(f"Bot post rejected: {msg}")


def post(text: str) -> None:
    """봇으로 TARGET_CHANNEL에 텍스트 메시지 게시. 길면 분할, 실패 시 재시도."""
    for chunk in _chunk(text):
        _send_one(chunk)
