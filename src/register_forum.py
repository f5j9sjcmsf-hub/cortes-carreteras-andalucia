"""Discover and securely register the eight provincial Telegram topics."""

from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from pathlib import Path

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_TIMEOUT_SECONDS
from forum_config import PROVINCES, save_forum_config
from logic import EVENT_CLOSED, format_message
from telegram import MESSAGE_INTERVAL_SECONDS, send_message


COMMAND = re.compile(r"^/registrar(?:@[A-Za-z0-9_]+)?\s+(.+?)\s*$", re.I)


def _key(value):
    normalized = unicodedata.normalize("NFKD", str(value).strip())
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


PROVINCE_BY_KEY = {_key(province): province for province in PROVINCES}


def _api(method, **params):
    endpoint = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        response = requests.post(endpoint, data=params, timeout=TELEGRAM_TIMEOUT_SECONDS)
        payload = response.json()
    except (requests.RequestException, ValueError):
        raise RuntimeError("No se pudo consultar Telegram durante el registro.") from None
    if response.status_code >= 400 or not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("Telegram rechazó el registro de los temas provinciales.")
    return payload.get("result")


def discover():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Falta el secreto TELEGRAM_BOT_TOKEN.")
    webhook = _api("getWebhookInfo")
    if isinstance(webhook, dict) and webhook.get("url"):
        raise RuntimeError("El bot tiene un webhook activo y no puede leer los comandos pendientes.")

    updates = _api("getUpdates", timeout=0, allowed_updates='["message"]')
    if not isinstance(updates, list):
        raise RuntimeError("Telegram no devolvió una lista de actualizaciones válida.")

    found = {}
    maximum_update_id = None
    for update in updates:
        if not isinstance(update, dict):
            continue
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            maximum_update_id = max(maximum_update_id or update_id, update_id)
        message = update.get("message")
        if not isinstance(message, dict):
            continue
        text = message.get("text")
        match = COMMAND.match(text) if isinstance(text, str) else None
        if not match:
            continue
        province = PROVINCE_BY_KEY.get(_key(match.group(1)))
        chat = message.get("chat")
        thread_id = message.get("message_thread_id")
        if (
            province
            and isinstance(chat, dict)
            and chat.get("type") == "supergroup"
            and chat.get("is_forum") is True
            and isinstance(chat.get("id"), int)
            and isinstance(thread_id, int)
            and thread_id > 0
        ):
            found[province] = (chat["id"], thread_id)

    missing = [province for province in PROVINCES if province not in found]
    if missing:
        raise RuntimeError("Faltan comandos válidos en: " + ", ".join(missing) + ".")
    group_ids = {value[0] for value in found.values()}
    if len(group_ids) != 1:
        raise RuntimeError("Los ocho temas no pertenecen al mismo supergrupo.")

    return {
        "version": 1,
        "group_chat_id": str(group_ids.pop()),
        "topics": {province: found[province][1] for province in PROVINCES},
    }, maximum_update_id


def load_active(path):
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    active = raw.get("active", {}) if isinstance(raw, dict) else {}
    if not isinstance(active, dict):
        return []
    return [item for item in active.values() if isinstance(item, dict)]


def verify_destinations(config, active):
    by_province = {province: [] for province in PROVINCES}
    for closure in active:
        province = closure.get("province")
        if province in by_province:
            by_province[province].append(closure)

    sent = 0
    for province in PROVINCES:
        confirmation = (
            "✅ <b>Tema provincial vinculado</b>\n\n"
            f"Los avisos de {province} se publicarán también en este tema."
        )
        send_message(
            confirmation,
            chat_id=config["group_chat_id"],
            message_thread_id=config["topics"][province],
        )
        sent += 1
        for closure in by_province[province]:
            time.sleep(MESSAGE_INTERVAL_SECONDS)
            send_message(
                format_message(closure, EVENT_CLOSED),
                chat_id=config["group_chat_id"],
                message_thread_id=config["topics"][province],
            )
            sent += 1
        time.sleep(MESSAGE_INTERVAL_SECONDS)
    return sent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config, maximum_update_id = discover()
    sent = verify_destinations(config, load_active(args.state))
    save_forum_config(args.output, config, TELEGRAM_BOT_TOKEN)
    if maximum_update_id is not None:
        _api("getUpdates", offset=maximum_update_id + 1, timeout=0)

    print("Ocho temas provinciales registrados y verificados.")
    print(f"Mensajes provinciales de comprobación enviados: {sent}")
    print("Configuración guardada cifrada; no se muestran identificadores.")


if __name__ == "__main__":
    main()

