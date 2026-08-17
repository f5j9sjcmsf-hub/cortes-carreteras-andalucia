import json
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = PROJECT_ROOT / "data" / "state.json"


def load_state():
    if not STATE_FILE.exists():
        return {}

    with STATE_FILE.open("r", encoding="utf-8") as handle:
        state = json.load(handle)

    if not isinstance(state, dict):
        raise ValueError("El estado operativo debe ser un objeto JSON.")
    return state


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")

    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(temporary, STATE_FILE)


