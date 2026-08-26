import math
import hashlib
import re
import time

import requests

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_FORUM_CONFIG_FILE,
    TELEGRAM_TIMEOUT_SECONDS,
)
from forum_config import load_forum_config


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
    if TELEGRAM_FORUM_CONFIG_FILE.exists():
        load_forum_config(TELEGRAM_FORUM_CONFIG_FILE, TELEGRAM_BOT_TOKEN)


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


def _send_chunk(endpoint, chunk, chat_id, message_thread_id=None):
    rate_limit_retries = 0
    while True:
        try:
            data = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
            if message_thread_id is not None:
                data["message_thread_id"] = str(message_thread_id)
            response = requests.post(
                endpoint,
                data=data,
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


def send_message(text, *, chat_id=None, message_thread_id=None):
    validate_configuration()
    endpoint = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    destination = TELEGRAM_CHAT_ID if chat_id is None else chat_id

    for index, chunk in enumerate(_split_message(text)):
        if index:
            time.sleep(MESSAGE_INTERVAL_SECONDS)
        _send_chunk(endpoint, chunk, destination, message_thread_id)


def send_messages(messages):
    for index, message in enumerate(messages):
        if index:
            time.sleep(MESSAGE_INTERVAL_SECONDS)
        send_message(message)


_PROVINCE_LINE = re.compile(r"(?m)^📍\s+([^<\n]+)\s*$")


def forum_is_configured():
    return TELEGRAM_FORUM_CONFIG_FILE.exists()


def _message_province(message):
    match = _PROVINCE_LINE.search(message)
    return match.group(1).strip() if match else ""


def build_forum_pending(messages):
    pending = []
    for message in messages:
        province = _message_province(message)
        if not province:
            continue
        pending.append(
            {
                "id": hashlib.sha256(message.encode("utf-8")).hexdigest()[:24],
                "province": province,
                "message": message,
            }
        )
    return pending


def merge_forum_pending(previous, current):
    merged = {}
    for item in [*(previous or []), *(current or [])]:
        if not isinstance(item, dict):
            continue
        identifier = item.get("id")
        province = item.get("province")
        message = item.get("message")
        if all(isinstance(value, str) and value for value in (
            identifier,
            province,
            message,
        )):
            merged[identifier] = {
                "id": identifier,
                "province": province,
                "message": message,
            }
    return [merged[key] for key in sorted(merged)]


def deliver_forum_pending(pending):
    config = load_forum_config(
        TELEGRAM_FORUM_CONFIG_FILE,
        TELEGRAM_BOT_TOKEN,
    )
    if config is None:
        return list(pending)

    remaining = []
    for index, item in enumerate(pending):
        if index:
            time.sleep(MESSAGE_INTERVAL_SECONDS)
        province = item["province"]
        thread_id = config["topics"].get(province)
        if thread_id is None:
            print(f"Aviso: no existe destino provincial para {province}.")
            remaining.append(item)
            continue
        try:
            send_message(
                item["message"],
                chat_id=config["group_chat_id"],
                message_thread_id=thread_id,
            )
        except RuntimeError as error:
            print(f"Aviso: falló el envío al tema de {province}: {error}")
            remaining.append(item)
    return remaining

