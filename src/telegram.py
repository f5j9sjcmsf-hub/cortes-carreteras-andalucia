import math
import time

import requests

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_TIMEOUT_SECONDS,
)


MAX_MESSAGE_LENGTH = 4000
MESSAGE_INTERVAL_SECONDS = 1.1
MAX_RATE_LIMIT_RETRIES = 5
DEFAULT_RETRY_AFTER_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 120.0


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


def _status_code(response):
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else 0


def _json_payload(response):
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _api_error_code(payload):
    if not payload:
        return 0
    code = payload.get("error_code")
    return code if isinstance(code, int) else 0


def _retry_after(payload):
    parameters = payload.get("parameters", {}) if payload else {}
    value = parameters.get("retry_after") if isinstance(parameters, dict) else None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS
    if not math.isfinite(seconds) or seconds <= 0:
        return DEFAULT_RETRY_AFTER_SECONDS
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def _safe_description(payload):
    description = payload.get("description", "") if payload else ""
    if not isinstance(description, str):
        return ""
    if TELEGRAM_BOT_TOKEN:
        description = description.replace(TELEGRAM_BOT_TOKEN, "[oculto]")
    return " ".join(description.split())[:300]


def _telegram_error(status, payload):
    code = _api_error_code(payload) or status
    description = _safe_description(payload)
    diagnostic = f"error {code}" if code else "respuesta no confirmada"
    if status:
        diagnostic += f", HTTP {status}"
    if description:
        diagnostic += f": {description}"
    return RuntimeError(f"Telegram rechazó el envío ({diagnostic}).")


def _send_chunk(endpoint, chunk):
    rate_limit_retries = 0
    while True:
        try:
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
        except requests.RequestException:
            raise RuntimeError(
                "No se pudo conectar con Telegram para enviar el mensaje."
            ) from None

        status = _status_code(response)
        payload = _json_payload(response)
        error_code = _api_error_code(payload)

        if status == 429 or error_code == 429:
            if rate_limit_retries >= MAX_RATE_LIMIT_RETRIES:
                raise RuntimeError(
                    "Telegram mantuvo el límite de frecuencia (HTTP 429) "
                    f"tras {MAX_RATE_LIMIT_RETRIES} reintentos."
                )
            rate_limit_retries += 1
            time.sleep(_retry_after(payload))
            continue

        if 400 <= status < 600:
            raise _telegram_error(status, payload) from None
        if payload is None:
            raise RuntimeError(
                "Telegram devolvió una respuesta no válida al enviar el mensaje."
            )
        if payload.get("ok") is not True:
            raise _telegram_error(status, payload) from None
        return


def send_message(text):
    validate_configuration()
    endpoint = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    for index, chunk in enumerate(_split_message(text)):
        if index:
            time.sleep(MESSAGE_INTERVAL_SECONDS)
        _send_chunk(endpoint, chunk)


def send_messages(messages):
    for index, message in enumerate(messages):
        if index:
            time.sleep(MESSAGE_INTERVAL_SECONDS)
        send_message(message)

