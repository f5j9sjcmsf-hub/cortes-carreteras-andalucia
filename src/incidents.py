"""Incident history and compact Telegram summaries."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Mapping, Sequence

from config import TIMEZONE
from logic import (
    EVENT_CLOSED,
    EVENT_PARTIAL_REOPEN,
    EVENT_REOPENED,
    EVENT_UPDATED,
    format_message,
    normalize_closure,
)


MAX_VISIBLE_HISTORY = 12
MAX_DESCRIPTION_LENGTH = 180


def history_entry(
    event: str,
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    event_at: str,
) -> dict[str, str]:
    item = normalize_closure(current)
    before = normalize_closure(previous) if previous else None
    if event == EVENT_CLOSED:
        description = f"Corte comunicado por {item['reason'] or 'motivo no indicado'}"
    elif event == EVENT_REOPENED:
        description = "Carretera reabierta"
    elif event == EVENT_PARTIAL_REOPEN:
        description = (
            "Reapertura parcial; permanece cortado el sentido "
            f"{_direction(item['direction']).casefold()}"
        )
    else:
        description = _describe_changes(before, item)
    return {
        "event": event,
        "at": event_at,
        "description": _shorten(description),
    }


def new_incident(key: str, closure: Mapping[str, Any], event_at: str) -> dict[str, Any]:
    item = normalize_closure(closure)
    return {
        "key": key,
        "initial_closure": item,
        "latest_closure": item,
        "started_at": item["published_at"] or event_at,
        "history": [history_entry(EVENT_CLOSED, None, item, item["published_at"] or event_at)],
        "destinations": {},
    }


def append_event(
    incident: Mapping[str, Any],
    event: str,
    closure: Mapping[str, Any],
    event_at: str,
) -> dict[str, Any]:
    updated = {
        **dict(incident),
        "history": [dict(item) for item in incident.get("history", [])],
        "destinations": {
            name: dict(value)
            for name, value in incident.get("destinations", {}).items()
            if isinstance(value, Mapping)
        },
    }
    previous = updated.get("latest_closure")
    item = normalize_closure(closure)
    updated["history"].append(history_entry(event, previous, item, event_at))
    updated["latest_closure"] = item
    return updated


def format_active_incident(incident: Mapping[str, Any]) -> str:
    item = incident["latest_closure"]
    base = format_message(item, EVENT_CLOSED)
    history = _history_lines(incident.get("history", []), include_closed=False)
    if not history:
        return base
    return base + "\n\n<b>Historial de la incidencia</b>\n" + "\n".join(history)


def format_final_summary(incident: Mapping[str, Any], reopened_at: str) -> str:
    item = normalize_closure(incident["latest_closure"])
    province = item["province"] or "Provincia no disponible"
    locality = " / ".join(item["localities"]) or "Localidad no disponible"
    road = item["road"] or "No indicada"
    reason = incident.get("initial_closure", {}).get("reason") or item["reason"] or "Motivo no indicado"
    kilometres = _kilometres(item.get("km_start", ""), item.get("km_end", ""))
    direction = _direction(item["direction"])
    history = list(incident.get("history", []))
    if not history or history[-1].get("event") != EVENT_REOPENED:
        history.append(history_entry(EVENT_REOPENED, item, item, reopened_at))
    lines = _history_lines(history, include_closed=True)
    duration = _duration(incident.get("started_at", ""), reopened_at)

    result = (
        "<b>🟢 CORTE FINALIZADO</b>\n\n"
        f"📍 {escape(province)}\n"
        f"<i>{escape(locality)}</i>\n\n"
        f"<b>{escape(road)}</b>, {escape(reason)}\n"
        f"<i>{escape(kilometres)}</i>\n"
        f"<i>{escape(direction)}</i>\n\n"
        "<b>Historial de la incidencia</b>\n"
        + "\n".join(lines)
    )
    if duration:
        result += f"\n\n<i>Duración total: {escape(duration)}</i>"
    return result


def format_reopen_notice(incident: Mapping[str, Any], reopened_at: str) -> str:
    item = normalize_closure(incident["latest_closure"])
    road = item["road"] or "Carretera"
    locality = " / ".join(item["localities"]) or item["province"]
    return (
        "<b>🟢 CARRETERA REABIERTA</b>\n\n"
        f"<b>{escape(road)}</b> · {escape(locality)}\n"
        f"<i>{escape(_display_time(reopened_at))}</i>\n\n"
        "El aviso principal contiene el resumen completo de la incidencia."
    )


def _describe_changes(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> str:
    if not previous:
        return "Información del corte actualizada"
    changes = []
    fields = (
        ("reason", "Motivo"),
        ("km_start", "PK inicial"),
        ("km_end", "PK final"),
        ("direction", "Sentido"),
        ("alternative", "Alternativa"),
        ("localities", "Municipio"),
    )
    for field, label in fields:
        before = previous.get(field)
        after = current.get(field)
        if before == after:
            continue
        if field == "direction":
            before, after = _direction(str(before)), _direction(str(after))
        elif field == "localities":
            before = " / ".join(before or [])
            after = " / ".join(after or [])
        if before and after:
            changes.append(f"{label}: {before} → {after}")
        elif after:
            changes.append(f"{label}: {after}")
        else:
            changes.append(f"{label}: retirado")
    return "; ".join(changes) or "Información del corte actualizada"


def _history_lines(history: Sequence[Mapping[str, Any]], *, include_closed: bool) -> list[str]:
    selected = [
        item for item in history
        if include_closed or item.get("event") != EVENT_CLOSED
    ]
    omitted = max(0, len(selected) - MAX_VISIBLE_HISTORY)
    selected = selected[-MAX_VISIBLE_HISTORY:]
    lines = []
    if omitted:
        lines.append(f"• … {omitted} cambios anteriores conservados en el registro")
    for item in selected:
        when = _display_time(str(item.get("at", "")))
        description = _shorten(str(item.get("description", "Actualización")))
        lines.append(f"• {escape(when)} — {escape(description)}")
    return lines


def _display_time(value: str) -> str:
    parsed = _parse_time(value)
    return parsed.strftime("%d/%m/%Y · %H:%M h") if parsed else "Fecha no indicada"


def _duration(start: str, end: str) -> str:
    started = _parse_time(start)
    finished = _parse_time(end)
    if not started or not finished or finished < started:
        return ""
    total_minutes = int((finished - started).total_seconds() // 60)
    days, remainder = divmod(total_minutes, 1440)
    hours, minutes = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days} día" if days == 1 else f"{days} días")
    if hours:
        parts.append(f"{hours} hora" if hours == 1 else f"{hours} horas")
    if minutes or not parts:
        parts.append(f"{minutes} minuto" if minutes == 1 else f"{minutes} minutos")
    return " y ".join(parts)


def _parse_time(value: str) -> datetime | None:
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


def _direction(value: str) -> str:
    return {
        "increasing": "Creciente",
        "decreasing": "Decreciente",
        "both": "Doble sentido",
        "unknown": "No indicado",
    }.get(value, value or "No indicado")


def _kilometres(start: str, end: str) -> str:
    def single(value: str) -> str:
        try:
            return f"{float(value):.3f}".replace(".", ",")
        except (TypeError, ValueError):
            return str(value)

    if not start and not end:
        return "Puntos kilométricos no indicados"
    if not start:
        return single(end)
    if not end or start == end:
        return single(start)
    return f"{single(start)}–{single(end)}"


def _shorten(value: str) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= MAX_DESCRIPTION_LENGTH:
        return cleaned
    return cleaned[: MAX_DESCRIPTION_LENGTH - 1].rstrip() + "…"

