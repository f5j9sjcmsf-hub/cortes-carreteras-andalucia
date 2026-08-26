from datetime import datetime

from config import DGT_TIMEOUT_SECONDS, DGT_URL, TIMEZONE
from dgt import fetch_closures
from logic import reconcile
from storage import load_state, save_state
from telegram import (
    build_forum_pending,
    deliver_forum_pending,
    forum_is_configured,
    merge_forum_pending,
    send_messages,
    validate_configuration,
)


def _now_iso():
    return datetime.now(TIMEZONE).isoformat(timespec="seconds")


def _initial_summary(count):
    noun = "corte activo" if count == 1 else "cortes activos"
    return (
        "<b>📋 ESTADO INICIAL DE CARRETERAS CORTADAS</b>\n\n"
        f"DGT publica {count} {noun} en Andalucía.\n"
        "A continuación se muestra la lista completa."
    )


def main():
    # Validate secrets even on a poll with no changes.  A broken deployment
    # must fail visibly instead of advancing its operational timestamp.
    validate_configuration()

    previous_state = load_state()
    closures = fetch_closures(
        url=DGT_URL,
        timeout=DGT_TIMEOUT_SECONDS,
    )
    was_initialized = bool(previous_state.get("initialized"))
    messages, next_state = reconcile(
        previous_state,
        closures,
        _now_iso(),
    )

    outgoing = list(messages)
    if not was_initialized:
        outgoing.insert(0, _initial_summary(len(closures)))

    # State becomes durable only after Telegram confirms every message.  If
    # an intermediate send fails, GitHub Actions exits and the prior state is
    # retried during the next run.
    send_messages(outgoing)
    if forum_is_configured():
        pending = merge_forum_pending(
            previous_state.get("forum_pending", []),
            build_forum_pending(outgoing),
        )
        next_state["forum_pending"] = deliver_forum_pending(pending)
    save_state(next_state)

    print(f"Cierres completos activos: {len(closures)}")
    print(f"Mensajes enviados: {len(outgoing)}")
    if forum_is_configured():
        print(
            "Envíos provinciales pendientes: "
            f"{len(next_state.get('forum_pending', []))}"
        )
    print("Estado guardado correctamente.")


if __name__ == "__main__":
    main()

