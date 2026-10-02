from datetime import datetime

from config import DGT_TIMEOUT_SECONDS, DGT_URL, TIMEZONE
from dgt import fetch_closures
from incident_delivery import process_incident_events
from logic import reconcile_events
from storage import load_state, save_state
from telegram import send_message, validate_configuration


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
    now_iso = _now_iso()
    events, next_state = reconcile_events(
        previous_state,
        closures,
        now_iso,
    )
    if not was_initialized:
        send_message(_initial_summary(len(closures)))

    next_state = process_incident_events(
        previous_state,
        next_state,
        events,
        now_iso,
    )
    save_state(next_state)

    print(f"Cierres completos activos: {len(closures)}")
    print(f"Eventos detectados: {len(events)}")
    print(f"Incidencias gestionadas: {len(next_state.get('telegram_incidents', {}))}")
    print(f"Avisos temporales pendientes: {len(next_state.get('telegram_cleanup', []))}")
    print("Estado guardado correctamente.")


if __name__ == "__main__":
    main()

