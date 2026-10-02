"""Deliver tracked road incidents while keeping Telegram topics tidy."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Mapping

from config import TELEGRAM_CHAT_ID, TIMEZONE
from incidents import (
    append_event,
    format_active_incident,
    format_final_summary,
    format_reopen_notice,
    new_incident,
)
from logic import EVENT_CLOSED, EVENT_REOPENED
from telegram import (
    delete_message,
    edit_message,
    forum_destination,
    send_message,
)


ALERT_RETENTION_HOURS = 36
SAFE_DELETE_HOURS = 47
MAX_CLEANUP_AGE_DAYS = 7
MAX_ARCHIVE_ITEMS = 200


def process_incident_events(
    previous_state: Mapping[str, Any],
    next_state: dict[str, Any],
    events: list[Mapping[str, Any]],
    now_iso: str,
) -> dict[str, Any]:
    """Apply structured events and retain enough state for safe retries."""

    incidents = {
        str(key): deepcopy(value)
        for key, value in previous_state.get("telegram_incidents", {}).items()
        if isinstance(value, Mapping)
    }
    cleanup = [
        deepcopy(item)
        for item in previous_state.get("telegram_cleanup", [])
        if isinstance(item, Mapping)
    ]
    archive = [
        deepcopy(item)
        for item in previous_state.get("telegram_archive", [])
        if isinstance(item, Mapping)
    ]

    cleanup = _run_cleanup(cleanup, now_iso)
    _retry_existing(incidents, cleanup, archive, now_iso)

    for event in events:
        key = str(event["key"])
        event_type = str(event["event"])
        if event_type == EVENT_CLOSED:
            incident = incidents.get(key) or new_incident(
                key,
                event["closure"],
                str(event["event_at"]),
            )
            incidents[key] = incident
            _ensure_active_delivery(incident, now_iso)
            continue

        incident = incidents.get(key)
        if incident is None:
            _deliver_legacy_event(event)
            continue

        incident = append_event(
            incident,
            event_type,
            event["closure"],
            str(event["event_at"]),
        )
        incidents[key] = incident

        if event_type == EVENT_REOPENED:
            incident["status"] = "closing"
            incident["reopened_at"] = str(event["event_at"])
            _finalize_incident(incident, cleanup, now_iso)
        else:
            _ensure_active_delivery(incident, now_iso)
            _add_alert(incident, event, now_iso)
            _deliver_alerts(incident)

    _collect_finalized(incidents, archive)
    next_state["telegram_incidents"] = incidents
    next_state["telegram_cleanup"] = cleanup
    next_state["telegram_archive"] = archive[-MAX_ARCHIVE_ITEMS:]
    return next_state


def _retry_existing(incidents, cleanup, archive, now_iso):
    for incident in list(incidents.values()):
        if incident.get("status") == "closing":
            _finalize_incident(incident, cleanup, now_iso)
        else:
            _expire_incident_alerts(incident, now_iso)
            _ensure_active_delivery(incident, now_iso)
            _deliver_alerts(incident)
    _collect_finalized(incidents, archive)


def _destination_specs(province):
    specs = {
        "channel": {
            "chat_id": TELEGRAM_CHAT_ID,
            "message_thread_id": None,
        }
    }
    forum = forum_destination(province)
    if forum:
        specs["forum"] = forum
    return specs


def _ensure_active_delivery(incident, now_iso):
    text = format_active_incident(incident)
    content_hash = _hash(text)
    province = incident["latest_closure"].get("province", "")
    destinations = incident.setdefault("destinations", {})
    for name, spec in _destination_specs(province).items():
        destination = destinations.setdefault(name, {**spec})
        destination.update(spec)
        message_id = destination.get("primary_id")
        try:
            if not isinstance(message_id, int):
                result = send_message(
                    text,
                    chat_id=spec["chat_id"],
                    message_thread_id=spec.get("message_thread_id"),
                )
                destination["primary_id"] = result["message_id"]
                destination["primary_sent_at"] = now_iso
                destination["content_hash"] = content_hash
            elif destination.get("content_hash") != content_hash:
                edit_message(spec["chat_id"], message_id, text)
                destination["content_hash"] = content_hash
        except RuntimeError as error:
            print(f"Aviso: no se pudo sincronizar el aviso {name}: {error}")


def _add_alert(incident, event, now_iso):
    alert_id = _hash(
        f"{event['event']}\0{event['event_at']}\0{event['message']}"
    )[:24]
    alerts = incident.setdefault("alerts", [])
    if any(item.get("id") == alert_id for item in alerts):
        return
    alerts.append(
        {
            "id": alert_id,
            "message": event["message"],
            "created_at": now_iso,
            "delete_after": _add_hours(now_iso, ALERT_RETENTION_HOURS),
            "sent": {},
        }
    )


def _deliver_alerts(incident):
    destinations = incident.get("destinations", {})
    for alert in incident.get("alerts", []):
        sent = alert.setdefault("sent", {})
        for name, destination in destinations.items():
            if isinstance(sent.get(name), int):
                continue
            primary_id = destination.get("primary_id")
            if not isinstance(primary_id, int):
                continue
            try:
                result = send_message(
                    alert["message"],
                    chat_id=destination["chat_id"],
                    message_thread_id=destination.get("message_thread_id"),
                    reply_to_message_id=primary_id,
                )
                sent[name] = result["message_id"]
            except RuntimeError as error:
                print(f"Aviso: no se pudo publicar la actualización {name}: {error}")


def _expire_incident_alerts(incident, now_iso):
    now = _parse_time(now_iso)
    destinations = incident.get("destinations", {})
    remaining = []
    for alert in incident.get("alerts", []):
        deadline = _parse_time(str(alert.get("delete_after") or ""))
        if not now or not deadline or now < deadline:
            remaining.append(alert)
            continue
        created = _parse_time(str(alert.get("created_at") or ""))
        failed = False
        for name, message_id in list(alert.get("sent", {}).items()):
            destination = destinations.get(name, {})
            if not isinstance(message_id, int) or not destination.get("chat_id"):
                continue
            try:
                delete_message(destination["chat_id"], message_id)
                alert["sent"].pop(name, None)
            except RuntimeError as error:
                failed = True
                print(f"Aviso: se reintentará retirar una actualización: {error}")
        if failed and created and now - created < timedelta(days=MAX_CLEANUP_AGE_DAYS):
            remaining.append(alert)
    incident["alerts"] = remaining


def _finalize_incident(incident, cleanup, now_iso):
    reopened_at = str(incident.get("reopened_at") or now_iso)
    summary = format_final_summary(incident, reopened_at)
    notice = format_reopen_notice(incident, reopened_at)
    destinations = incident.setdefault("destinations", {})
    province = incident["latest_closure"].get("province", "")
    for name, spec in _destination_specs(province).items():
        destination = destinations.setdefault(name, {**spec})
        destination.update(spec)
        if destination.get("finalized") is True:
            continue

        _delete_incident_alerts(incident, name, destination, cleanup, now_iso)
        primary_id = destination.get("primary_id")
        primary_sent_at = str(destination.get("primary_sent_at") or "")
        young_enough = _age_hours(primary_sent_at, now_iso) < SAFE_DELETE_HOURS
        try:
            if isinstance(primary_id, int) and young_enough:
                try:
                    delete_message(destination["chat_id"], primary_id)
                    destination["primary_id"] = None
                except RuntimeError:
                    young_enough = False

            if isinstance(destination.get("primary_id"), int):
                edit_message(destination["chat_id"], destination["primary_id"], summary)
                result = send_message(
                    notice,
                    chat_id=destination["chat_id"],
                    message_thread_id=destination.get("message_thread_id"),
                    reply_to_message_id=destination["primary_id"],
                )
                cleanup.append(
                    _cleanup_item(
                        name,
                        destination["chat_id"],
                        result["message_id"],
                        now_iso,
                    )
                )
                destination["final_message_id"] = destination["primary_id"]
            else:
                result = send_message(
                    summary,
                    chat_id=destination["chat_id"],
                    message_thread_id=destination.get("message_thread_id"),
                )
                destination["final_message_id"] = result["message_id"]
            destination["finalized"] = True
        except RuntimeError as error:
            print(f"Aviso: no se pudo finalizar la incidencia {name}: {error}")

    if destinations and all(
        value.get("finalized") is True for value in destinations.values()
    ):
        incident["status"] = "finalized"
        incident["final_summary"] = summary
        incident["finalized_at"] = now_iso


def _delete_incident_alerts(
    incident,
    destination_name,
    destination,
    cleanup,
    now_iso,
):
    for alert in incident.get("alerts", []):
        message_id = alert.get("sent", {}).get(destination_name)
        if not isinstance(message_id, int):
            continue
        try:
            delete_message(destination["chat_id"], message_id)
        except RuntimeError as error:
            print(f"Aviso: no se pudo retirar una actualización: {error}")
            cleanup.append(
                {
                    "destination": destination_name,
                    "chat_id": destination["chat_id"],
                    "message_id": message_id,
                    "created_at": alert.get("created_at") or now_iso,
                    "delete_after": now_iso,
                }
            )
        alert["sent"].pop(destination_name, None)


def _collect_finalized(incidents, archive):
    for key in list(incidents):
        incident = incidents[key]
        if incident.get("status") != "finalized":
            continue
        archive.append(
            {
                "key": key,
                "started_at": incident.get("started_at"),
                "finalized_at": incident.get("finalized_at"),
                "initial_closure": incident.get("initial_closure"),
                "latest_closure": incident.get("latest_closure"),
                "history": incident.get("history", []),
                "final_summary": incident.get("final_summary", ""),
            }
        )
        del incidents[key]


def _run_cleanup(items, now_iso):
    remaining = []
    now = _parse_time(now_iso)
    for item in items:
        delete_after = _parse_time(str(item.get("delete_after") or ""))
        created_at = _parse_time(str(item.get("created_at") or ""))
        if delete_after is None or now is None or now < delete_after:
            remaining.append(item)
            continue
        try:
            delete_message(item["chat_id"], int(item["message_id"]))
        except RuntimeError as error:
            if created_at and now - created_at < timedelta(days=MAX_CLEANUP_AGE_DAYS):
                print(f"Aviso: se reintentará retirar un aviso temporal: {error}")
                remaining.append(item)
    return remaining


def _deliver_legacy_event(event):
    province = event["closure"].get("province", "")
    try:
        send_message(event["message"])
    except RuntimeError as error:
        print(f"Aviso: falló un mensaje heredado del canal: {error}")
    forum = forum_destination(province)
    if forum:
        try:
            send_message(
                event["message"],
                chat_id=forum["chat_id"],
                message_thread_id=forum["message_thread_id"],
            )
        except RuntimeError as error:
            print(f"Aviso: falló un mensaje heredado provincial: {error}")


def _cleanup_item(name, chat_id, message_id, now_iso):
    return {
        "destination": name,
        "chat_id": chat_id,
        "message_id": message_id,
        "created_at": now_iso,
        "delete_after": _add_hours(now_iso, ALERT_RETENTION_HOURS),
    }


def _hash(value):
    return sha256(str(value).encode("utf-8")).hexdigest()


def _add_hours(value, hours):
    parsed = _parse_time(value) or datetime.now(TIMEZONE)
    return (parsed + timedelta(hours=hours)).isoformat(timespec="seconds")


def _age_hours(value, now_iso):
    started = _parse_time(value)
    now = _parse_time(now_iso)
    if not started or not now:
        return float("inf")
    return max(0.0, (now - started).total_seconds() / 3600)


def _parse_time(value):
    if not value:
        return None
    candidate = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TIMEZONE)
    return parsed.astimezone(TIMEZONE)

