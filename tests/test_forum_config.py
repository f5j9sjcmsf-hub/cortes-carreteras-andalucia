import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from forum_config import PROVINCES, load_forum_config, save_forum_config


def sample_config():
    return {
        "version": 1,
        "group_chat_id": "-1001234567890",
        "topics": {
            province: index + 10 for index, province in enumerate(PROVINCES)
        },
    }


def test_forum_config_is_encrypted_and_round_trips(tmp_path):
    path = tmp_path / "telegram_forum.enc"
    config = sample_config()
    save_forum_config(path, config, "123456:token-secreto")

    raw = path.read_text(encoding="ascii")
    assert "-1001234567890" not in raw
    assert "Granada" not in raw
    assert load_forum_config(path, "123456:token-secreto") == config


def test_wrong_token_cannot_decrypt(tmp_path):
    path = tmp_path / "telegram_forum.enc"
    save_forum_config(path, sample_config(), "123456:token-secreto")
    with pytest.raises(RuntimeError, match="descifrar"):
        load_forum_config(path, "otro-token")

