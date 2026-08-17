import requests

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_TIMEOUT_SECONDS,
)


MAX_MESSAGE_LENGTH = 4000


def _split_message(text, limit=MAX_MESSAGE_LENGTH):
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = limit
        else:
            split_at += 1

        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]

    return chunks


def validate_configuration():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Falta el secreto TELEGRAM_BOT_TOKEN.")
    if not TELEGRAM_CHAT_ID:
        raise RuntimeError("Falta el secreto TELEGRAM_CHAT_ID.")


def send_message(text):
    validate_configuration()
    endpoint = (
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    for chunk in _split_message(text):
        response = requests.post(
            endpoint,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
            timeout=TELEGRAM_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("ok") is not True:
            raise RuntimeError(
                "Telegram no confirmó correctamente el envío del mensaje."
            )


def send_messages(messages):
    for message in messages:
        send_message(message)

