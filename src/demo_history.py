"""Accelerated visual demonstration of the tracked incident lifecycle."""

from datetime import datetime, timedelta
import time

from config import TIMEZONE
from incidents import append_event, format_active_incident, format_final_summary, new_incident
from logic import EVENT_REOPENED, EVENT_UPDATED, format_message
from telegram import delete_message, edit_message, forum_destination, send_message


PROVINCE = "Granada"
PREFIX = "🧪 <b>PRUEBA DEL NUEVO SISTEMA</b>\n<i>No corresponde a un corte real.</i>\n\n"


def iso(value):
    return value.isoformat(timespec="seconds")


def main():
    started = datetime.now(TIMEZONE)
    initial = {
        "source_ids": ["demo-history"],
        "situation_ids": ["demo-history"],
        "record_ids": ["demo-history"],
        "province": PROVINCE,
        "localities": ["Demostración"],
        "road": "VÍA DE PRUEBA",
        "km_start": 10,
        "km_end": 12,
        "direction": "decreasing",
        "reason": "Prueba de funcionamiento",
        "cause_code": "demo",
        "detail_code": "demo",
        "published_at": iso(started),
        "source_updated_at": iso(started),
        "alternative": "",
    }
    destination = forum_destination(PROVINCE)
    if not destination:
        raise RuntimeError(f"No se encontró el tema provincial de {PROVINCE}.")

    incident = new_incident("demo-history", initial, iso(started))
    primary = send_message(
        PREFIX + format_active_incident(incident),
        chat_id=destination["chat_id"],
        message_thread_id=destination["message_thread_id"],
    )

    time.sleep(15)
    updated_at = started + timedelta(minutes=25)
    updated = {**initial, "alternative": "Paso alternativo habilitado", "source_updated_at": iso(updated_at)}
    incident = append_event(incident, EVENT_UPDATED, updated, iso(updated_at))
    edit_message(
        destination["chat_id"],
        primary["message_id"],
        PREFIX + format_active_incident(incident),
    )
    update_alert = send_message(
        PREFIX + format_message(updated, EVENT_UPDATED, event_at=iso(updated_at)),
        chat_id=destination["chat_id"],
        message_thread_id=destination["message_thread_id"],
        reply_to_message_id=primary["message_id"],
    )

    time.sleep(15)
    reopened_at = started + timedelta(hours=1)
    incident = append_event(incident, EVENT_REOPENED, updated, iso(reopened_at))
    delete_message(destination["chat_id"], update_alert["message_id"])
    delete_message(destination["chat_id"], primary["message_id"])
    final = send_message(
        PREFIX + format_final_summary(incident, iso(reopened_at)),
        chat_id=destination["chat_id"],
        message_thread_id=destination["message_thread_id"],
    )
    print("Demostración completada: cierre, actualización, limpieza y resumen final.")
    print(f"Resumen final confirmado por Telegram: {bool(final.get('message_id'))}")


if __name__ == "__main__":
    main()

