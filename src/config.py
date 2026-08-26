import os
from pathlib import Path
from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("Europe/Madrid")

DGT_URL = os.environ.get(
    "DGT_URL",
    "https://nap.dgt.es/datex2/v3/dgt/"
    "SituationPublication/datex2_v37.xml",
).strip()
DGT_TIMEOUT_SECONDS = 45

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_TIMEOUT_SECONDS = 30
TELEGRAM_FORUM_CONFIG_FILE = Path(
    os.environ.get(
        "TELEGRAM_FORUM_CONFIG_FILE",
        str(Path(__file__).resolve().parents[1] / "data" / "telegram_forum.enc"),
    )
)

BOT_NAME = "Carreteras cortadas - Andalucía"

