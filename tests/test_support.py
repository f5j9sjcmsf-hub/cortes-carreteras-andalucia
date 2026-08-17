import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import storage
import telegram


class StorageTests(unittest.TestCase):
    def test_state_path_is_independent_of_working_directory(self):
        self.assertEqual(storage.STATE_FILE, PROJECT_ROOT / "data" / "state.json")

    def test_round_trip_uses_valid_utf8_json_and_is_atomic(self):
        original = storage.STATE_FILE
        try:
            with tempfile.TemporaryDirectory() as directory:
                storage.STATE_FILE = Path(directory) / "state.json"
                expected = {"initialized": True, "locality": "Güéjar Sierra"}
                storage.save_state(expected)
                self.assertEqual(storage.load_state(), expected)
                raw = storage.STATE_FILE.read_text(encoding="utf-8")
                self.assertEqual(json.loads(raw), expected)
                self.assertFalse(
                    storage.STATE_FILE.with_suffix(".json.tmp").exists()
                )
        finally:
            storage.STATE_FILE = original

    def test_non_object_state_is_rejected(self):
        original = storage.STATE_FILE
        try:
            with tempfile.TemporaryDirectory() as directory:
                storage.STATE_FILE = Path(directory) / "state.json"
                storage.STATE_FILE.write_text("[]", encoding="utf-8")
                with self.assertRaises(ValueError):
                    storage.load_state()
        finally:
            storage.STATE_FILE = original


class TelegramTests(unittest.TestCase):
    def test_long_messages_split_on_line_boundaries(self):
        text = ("<b>carretera</b>\n" * 400).strip()
        chunks = telegram._split_message(text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 4000 for chunk in chunks))
        self.assertEqual("".join(chunks), text)

    def test_long_unbroken_line_is_split_safely(self):
        text = "x" * 9001
        chunks = telegram._split_message(text)
        self.assertEqual([4000, 4000, 1001], [len(chunk) for chunk in chunks])
        self.assertEqual(text, "".join(chunks))

    @patch.object(telegram, "TELEGRAM_CHAT_ID", "@canal")
    @patch.object(telegram, "TELEGRAM_BOT_TOKEN", "token-secreto")
    @patch("telegram.requests.post")
    def test_send_message_requires_telegram_confirmation(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": False}
        post.return_value = response

        with self.assertRaises(RuntimeError):
            telegram.send_message("mensaje")

    @patch.object(telegram, "TELEGRAM_CHAT_ID", "")
    @patch.object(telegram, "TELEGRAM_BOT_TOKEN", "")
    def test_missing_secrets_fail_before_network_request(self):
        with patch("telegram.requests.post") as post:
            with self.assertRaises(RuntimeError):
                telegram.send_message("mensaje")
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main()

