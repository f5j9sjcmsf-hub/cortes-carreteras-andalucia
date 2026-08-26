"""Encrypted configuration for the provincial Telegram forum topics."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from cryptography.fernet import Fernet, InvalidToken


PROVINCES = (
    "Almería",
    "Cádiz",
    "Córdoba",
    "Granada",
    "Huelva",
    "Jaén",
    "Málaga",
    "Sevilla",
)


def _fernet(token: str) -> Fernet:
    if not token:
        raise RuntimeError("Falta el secreto TELEGRAM_BOT_TOKEN.")
    digest = hashlib.sha256(
        b"carreteras-andalucia-forum-v1\0" + token.encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _validate(raw: Mapping[str, Any]) -> dict[str, Any]:
    if raw.get("version") != 1:
        raise ValueError("La configuración provincial tiene una versión no válida.")

    group_chat_id = raw.get("group_chat_id")
    topics = raw.get("topics")
    if not isinstance(group_chat_id, (str, int)) or not str(group_chat_id).strip():
        raise ValueError("Falta el identificador del grupo provincial.")
    if not isinstance(topics, Mapping):
        raise ValueError("Falta el mapa de temas provinciales.")

    cleaned_topics: dict[str, int] = {}
    for province in PROVINCES:
        thread_id = topics.get(province)
        if not isinstance(thread_id, int) or thread_id <= 0:
            raise ValueError(f"Falta el tema de {province}.")
        cleaned_topics[province] = thread_id

    if set(topics) != set(PROVINCES):
        raise ValueError("El mapa de temas debe contener exactamente ocho provincias.")

    return {
        "version": 1,
        "group_chat_id": str(group_chat_id).strip(),
        "topics": cleaned_topics,
    }


def encrypt_forum_config(config: Mapping[str, Any], token: str) -> bytes:
    validated = _validate(config)
    plaintext = json.dumps(
        validated,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _fernet(token).encrypt(plaintext) + b"\n"


def save_forum_config(path: Path, config: Mapping[str, Any], token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encrypt_forum_config(config, token))
    temporary.replace(path)


def load_forum_config(path: Path, token: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        plaintext = _fernet(token).decrypt(path.read_bytes().strip())
        raw = json.loads(plaintext.decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError(
            "No se pudo descifrar la configuración provincial de Telegram."
        ) from None
    if not isinstance(raw, Mapping):
        raise RuntimeError("La configuración provincial de Telegram no es válida.")
    try:
        return _validate(raw)
    except ValueError as error:
        raise RuntimeError(str(error)) from None

