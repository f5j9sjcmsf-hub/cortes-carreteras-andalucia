"""Pure reconciliation logic for the Andalusian road-closure monitor.

The functions in this module do not perform I/O.  They return both the
messages that should be sent and the state that should be committed *after*
those messages have been sent successfully.  This keeps a monitoring run
transactional and makes retries safe: abandoning ``next_state`` leaves the
previous baseline untouched.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html import escape
import json
import re
from typing import Any, Iterable, Mapping, Sequence


STATE_VERSION = 1

EVENT_CLOSED = "closed"
EVENT_UPDATED = "updated"
EVENT_PARTIAL_REOPEN = "partial_reopen"
EVENT_REOPENED = "reopened"

EVENT_TITLES = {
    EVENT_CLOSED: "🔴 CARRETERA CORTADA",
    EVENT_UPDATED: "🟠 CORTE ACTUALIZADO",
    EVENT_PARTIAL_REOPEN: "🟠 REAPERTURA PARCIAL",
    EVENT_REOPENED: "🟢 CARRETERA REABIERTA",
}

EVENT_DATE_LABELS = {
    EVENT_CLOSED: "Publicado",
    EVENT_UPDATED: "Actualizado",
    EVENT_PARTIAL_REOPEN: "Reapertura parcial",
    EVENT_REOPENED: "Reabierto",
}

_CLOSURE_FIELDS = (
    "source_ids",
    "situation_ids",
    "record_ids",
    "province",
    "localities",
    "road",
    "km_start",
    "km_end",
    "direction",
    "reason",
    "cause_code",
    "detail_code",
    "published_at",
    "source_updated_at",
    "alternative",
)

# Only user-visible changes produce a Telegram update.  Identifiers and DGT
# internal codes are still refreshed in state, but do not create noisy alerts.
_VISIBLE_FIELDS = (
    "province",
    "localities",
    "reason",
    "road",
    "direction",
    "km_start",
    "km_end",
    "published_at",
    "alternative",
)

_DIRECTION_LABELS = {
    "increasing": "Creciente",
    "decreasing": "Decreciente",
    "both": "Doble sentido",
    "unknown": "No indicado",
}

_DIRECTION_ALIASES = {
    "increasing": "increasing",
    "increase": "increasing",
    "increasing direction": "increasing",
    "positive": "increasing",
    "creciente": "increasing",
    "ascendente": "increasing",
    "decreasing": "decreasing",
    "decrease": "decreasing",
    "decreasing direction": "decreasing",
    "negative": "decreasing",
    "decreciente": "decreasing",
    "descendente": "decreasing",
    "both": "both",
    "both directions": "both",
    "both direction": "both",
    "double": "both",
    "doble": "both",
    "doble sentido": "both",
    "ambos sentidos": "both",
    "unknown": "unknown",
    "": "unknown",
}


class UnsupportedStateVersion(ValueError):
    """Raised when persisted state was written by an incompatible version."""


def plan_initial(
    state: Mapping[str, Any] | None,
    current: Iterable[Mapping[str, Any]],
    now_iso: str,
) -> tuple[list[str], dict[str, Any]]:
    """Plan the initial full snapshot and create its baseline.

    Every currently active closure is emitted once.  If an initialized state
    is supplied accidentally, normal incremental reconciliation is used so a
    restart cannot republish the full snapshot.
    """

    _validate_state(state)
    if state and state.get("initialized"):
        return plan_changes(state, current, now_iso)

    closures = _deduplicate_current(current)
    active: dict[str, dict[str, Any]] = {}
    messages: list[str] = []

    for closure in closures:
        key = _unique_key(_new_active_key(closure), active)
        active[key] = _state_entry(
            closure,
            first_seen_at=now_iso,
            last_seen_at=now_iso,
            last_changed_at=now_iso,
        )
        messages.append(format_message(closure, EVENT_CLOSED))

    previous_revision = _safe_revision(state)
    next_state = {
        "version": STATE_VERSION,
        "initialized": True,
        "revision": previous_revision + 1,
        "initialized_at": now_iso,
        "updated_at": now_iso,
        "active": _sorted_mapping(active),
    }
    return messages, next_state


def plan_changes(
    state: Mapping[str, Any] | None,
    current: Iterable[Mapping[str, Any]],
    now_iso: str,
) -> tuple[list[str], dict[str, Any]]:
    """Plan one incremental reconciliation without mutating either input.

    Matching is one-to-one.  Shared ``source_ids`` are considered first,
    shared ``situation_ids`` second, and an exact physical segment (same road,
    province and kilometre range) is the final fallback.
    """

    _validate_state(state)
    if not state or not state.get("initialized"):
        return plan_initial(state, current, now_iso)

    old_active_raw = state.get("active", {})
    if not isinstance(old_active_raw, Mapping):
        raise ValueError("state['active'] must be a mapping")

    # Deep-copying here is intentional: callers can safely retain and compare
    # the state object they passed in, even when nested lists are present.
    old_active = {
        str(key): deepcopy(dict(value))
        for key, value in old_active_raw.items()
        if isinstance(value, Mapping)
    }
    old_closures = {
        key: normalize_closure(value) for key, value in old_active.items()
    }
    closures = _deduplicate_current(current)
    matches = _match_closures(old_closures, closures)

    next_active: dict[str, dict[str, Any]] = {}
    events: list[tuple[str, dict[str, Any], str]] = []
    matched_old = set(matches.values())

    for current_index, closure in enumerate(closures):
        old_key = matches.get(current_index)
        if old_key is None:
            key = _unique_key(_new_active_key(closure), {**old_active, **next_active})
            next_active[key] = _state_entry(
                closure,
                first_seen_at=now_iso,
                last_seen_at=now_iso,
                last_changed_at=now_iso,
            )
            events.append((EVENT_CLOSED, closure, now_iso))
            continue

        old_closure = old_closures[old_key]
        old_entry = old_active[old_key]
        changed = _visible_signature(old_closure) != _visible_signature(closure)
        if changed:
            if _is_partial_reopening(old_closure, closure):
                events.append((
                    EVENT_PARTIAL_REOPEN,
                    closure,
                    closure["source_updated_at"] or now_iso,
                ))
            else:
                events.append((
                    EVENT_UPDATED,
                    closure,
                    closure["source_updated_at"] or now_iso,
                ))

        next_active[old_key] = _state_entry(
            closure,
            first_seen_at=str(old_entry.get("first_seen_at") or now_iso),
            last_seen_at=now_iso,
            last_changed_at=(
                now_iso
                if changed
                else str(old_entry.get("last_changed_at") or now_iso)
            ),
        )

    for old_key, old_closure in old_closures.items():
        if old_key not in matched_old:
            events.append((EVENT_REOPENED, old_closure, now_iso))

    events.sort(key=lambda item: (_closure_sort_key(item[1]), _event_rank(item[0])))
    messages = [
        format_message(closure, event, event_at=event_at)
        for event, closure, event_at in events
    ]

    next_state = {
        "version": STATE_VERSION,
        "initialized": True,
        "revision": _safe_revision(state) + 1,
        "initialized_at": str(state.get("initialized_at") or now_iso),
        "updated_at": now_iso,
        "active": _sorted_mapping(next_active),
    }
    return messages, next_state


def reconcile(
    state: Mapping[str, Any] | None,
    current: Iterable[Mapping[str, Any]],
    now_iso: str,
) -> tuple[list[str], dict[str, Any]]:
    """Choose initial or incremental planning from the state's baseline flag."""

    if state and state.get("initialized"):
        return plan_changes(state, current, now_iso)
    return plan_initial(state, current, now_iso)


def normalize_closure(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deterministic, JSON-serialisable closure representation."""

    if not isinstance(raw, Mapping):
        raise TypeError("each closure must be a mapping")

    km_start = _normalise_km(raw.get("km_start"))
    km_end = _normalise_km(raw.get("km_end"))
    if km_start and km_end and _km_decimal(km_start) is not None and _km_decimal(km_end) is not None:
        if _km_decimal(km_start) > _km_decimal(km_end):
            km_start, km_end = km_end, km_start

    return {
        "source_ids": _normalise_ids(raw.get("source_ids")),
        "situation_ids": _normalise_ids(raw.get("situation_ids")),
        "record_ids": _normalise_ids(raw.get("record_ids")),
        "province": _clean_text(raw.get("province")),
        "localities": _normalise_localities(raw.get("localities")),
        "road": _clean_text(raw.get("road")).upper(),
        "km_start": km_start,
        "km_end": km_end,
        "direction": _normalise_direction(raw.get("direction")),
        "reason": _clean_text(raw.get("reason")),
        "cause_code": _clean_text(raw.get("cause_code")),
        "detail_code": _clean_text(raw.get("detail_code")),
        "published_at": _normalise_published_at(raw.get("published_at")),
        "source_updated_at": _normalise_published_at(
            raw.get("source_updated_at")
        ),
        "alternative": _clean_text(raw.get("alternative")),
    }


def format_message(
    closure: Mapping[str, Any],
    event: str,
    *,
    event_at: Any = None,
) -> str:
    """Render one Telegram message using HTML parse mode."""

    if event not in EVENT_TITLES:
        raise ValueError(f"unknown event type: {event}")
    item = normalize_closure(closure)
    province = item["province"] or "Provincia no disponible"
    locality = " / ".join(item["localities"]) or "Localidad no disponible"
    reason = item["reason"] or "Motivo no especificado"
    road = item["road"] or "No indicada"
    direction = _DIRECTION_LABELS[item["direction"]]
    kilometres = _format_kilometres(item["km_start"], item["km_end"])
    fallback_event_at = _normalise_published_at(event_at)
    if event == EVENT_CLOSED:
        timestamp = item["published_at"] or fallback_event_at
    elif event in {EVENT_UPDATED, EVENT_PARTIAL_REOPEN}:
        timestamp = item["source_updated_at"] or fallback_event_at
    else:
        timestamp = fallback_event_at
    formatted_timestamp = _format_published_at(timestamp)
    alternative = item["alternative"]

    alternative_line = ""
    if alternative and event != EVENT_REOPENED:
        alternative_line = (
            "\n\n↪️ <b>Alternativa:</b> "
            f"{escape(alternative)}"
        )

    return (
        f"<b>{EVENT_TITLES[event]}</b>\n\n"
        f"📍 {escape(province)}\n"
        f"<i>{escape(locality)}</i>\n\n"
        f"<b>{escape(road)}</b>, {escape(reason)}\n"
        f"<i>{escape(kilometres)}</i>\n"
        f"<i>{direction}</i>{alternative_line}\n\n"
        f"<i>{EVENT_DATE_LABELS[event]}: {escape(formatted_timestamp)}</i>"
    )


def _validate_state(state: Mapping[str, Any] | None) -> None:
    if state is None:
        return
    if not isinstance(state, Mapping):
        raise TypeError("state must be a mapping or None")
    version = state.get("version")
    if version is not None and version != STATE_VERSION:
        raise UnsupportedStateVersion(
            f"unsupported state version {version!r}; expected {STATE_VERSION}"
        )


def _safe_revision(state: Mapping[str, Any] | None) -> int:
    if not state:
        return 0
    revision = state.get("revision", 0)
    return revision if isinstance(revision, int) and revision >= 0 else 0


def _state_entry(
    closure: Mapping[str, Any],
    *,
    first_seen_at: str,
    last_seen_at: str,
    last_changed_at: str,
) -> dict[str, Any]:
    entry = normalize_closure(closure)
    entry.update(
        {
            "first_seen_at": first_seen_at,
            "last_seen_at": last_seen_at,
            "last_changed_at": last_changed_at,
        }
    )
    return entry


def _deduplicate_current(
    current: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for raw in current:
        closure = normalize_closure(raw)
        signature = _visible_signature(closure)
        existing = merged.get(signature)
        if existing is None:
            merged[signature] = closure
            continue
        for field in ("source_ids", "situation_ids", "record_ids"):
            existing[field] = sorted(
                set(existing[field]).union(closure[field]), key=str.casefold
            )
        for field in ("cause_code", "detail_code"):
            candidates = sorted(
                {value for value in (existing[field], closure[field]) if value},
                key=str.casefold,
            )
            existing[field] = candidates[0] if candidates else ""
    return sorted(merged.values(), key=_closure_sort_key)


def _match_closures(
    old: Mapping[str, Mapping[str, Any]],
    current: Sequence[Mapping[str, Any]],
) -> dict[int, str]:
    unmatched_old = set(old)
    unmatched_current = set(range(len(current)))
    result: dict[int, str] = {}

    # Separate passes make source-record continuity stronger than broad DGT
    # situation continuity, and both stronger than geographic fallback.
    predicates = (
        lambda previous, now: bool(
            set(previous["source_ids"]).intersection(now["source_ids"])
        ),
        lambda previous, now: bool(
            set(previous["situation_ids"]).intersection(now["situation_ids"])
        ),
        _same_physical_segment,
    )
    for predicate in predicates:
        pass_matches = _maximum_cardinality_pass(
            old,
            current,
            unmatched_old,
            unmatched_current,
            predicate,
        )
        for current_index, old_key in pass_matches.items():
            result[current_index] = old_key
            unmatched_current.remove(current_index)
            unmatched_old.remove(old_key)
    return result


def _maximum_cardinality_pass(
    old: Mapping[str, Mapping[str, Any]],
    current: Sequence[Mapping[str, Any]],
    old_indexes: set[str],
    current_indexes: set[int],
    predicate: Any,
) -> dict[int, str]:
    candidates: dict[int, list[str]] = {}
    for current_index in sorted(current_indexes):
        choices = [
            old_key
            for old_key in old_indexes
            if predicate(old[old_key], current[current_index])
        ]
        choices.sort(
            key=lambda old_key: (
                -_similarity_score(old[old_key], current[current_index]),
                old_key,
            )
        )
        if choices:
            candidates[current_index] = choices

    owner_by_old: dict[str, int] = {}

    def assign(current_index: int, visited: set[str]) -> bool:
        for old_key in candidates.get(current_index, []):
            if old_key in visited:
                continue
            visited.add(old_key)
            previous_owner = owner_by_old.get(old_key)
            if previous_owner is None or assign(previous_owner, visited):
                owner_by_old[old_key] = current_index
                return True
        return False

    # Constrained records go first.  The augmenting-path algorithm still finds
    # maximum cardinality while its stable ordering resolves ambiguous feeds.
    order = sorted(
        candidates,
        key=lambda index: (
            len(candidates[index]),
            _closure_sort_key(current[index]),
            index,
        ),
    )
    for current_index in order:
        assign(current_index, set())
    return {current_index: old_key for old_key, current_index in owner_by_old.items()}


def _similarity_score(old: Mapping[str, Any], new: Mapping[str, Any]) -> int:
    score = 0
    if set(old["source_ids"]).intersection(new["source_ids"]):
        score += 10_000
    if set(old["situation_ids"]).intersection(new["situation_ids"]):
        score += 1_000
    weights = {
        "road": 100,
        "province": 50,
        "km_start": 40,
        "km_end": 40,
        "direction": 20,
        "localities": 10,
        "reason": 5,
    }
    for field, weight in weights.items():
        if old[field] == new[field]:
            score += weight
    return score


def _same_physical_segment(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    # Missing geography is not a safe fallback identity.
    if not old["road"] or not new["road"] or not old["province"] or not new["province"]:
        return False
    if not (old["km_start"] or old["km_end"]):
        return False
    if not (new["km_start"] or new["km_end"]):
        return False
    return (
        old["road"] == new["road"]
        and old["province"].casefold() == new["province"].casefold()
        and old["km_start"] == new["km_start"]
        and old["km_end"] == new["km_end"]
    )


def _is_partial_reopening(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    if old["direction"] != "both" or new["direction"] not in {
        "increasing",
        "decreasing",
    }:
        return False
    old_sources = set(old["source_ids"])
    new_sources = set(new["source_ids"])
    # A real partial reopening removes one of the two opposite raw records.
    # Merely changing DATEX's direction label while retaining the same records
    # is an ordinary correction/update, not evidence that one side reopened.
    return len(old_sources) >= 2 and bool(new_sources) and new_sources < old_sources


def _visible_signature(closure: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        tuple(closure[field]) if field == "localities" else closure[field]
        for field in _VISIBLE_FIELDS
    )


def _new_active_key(closure: Mapping[str, Any]) -> str:
    if closure["source_ids"]:
        kind = "src"
        identity: Any = closure["source_ids"]
    elif closure["situation_ids"]:
        kind = "sit"
        identity = closure["situation_ids"]
    else:
        kind = "seg"
        identity = _visible_signature(closure)
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return f"{kind}-{sha256(encoded.encode('utf-8')).hexdigest()[:20]}"


def _unique_key(base: str, mappings: Mapping[str, Any]) -> str:
    if base not in mappings:
        return base
    counter = 2
    while f"{base}-{counter}" in mappings:
        counter += 1
    return f"{base}-{counter}"


def _sorted_mapping(mapping: Mapping[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {key: mapping[key] for key in sorted(mapping)}


def _event_rank(event: str) -> int:
    return {
        EVENT_CLOSED: 0,
        EVENT_UPDATED: 1,
        EVENT_PARTIAL_REOPEN: 2,
        EVENT_REOPENED: 3,
    }[event]


def _closure_sort_key(closure: Mapping[str, Any]) -> tuple[Any, ...]:
    direction_rank = {"both": 0, "increasing": 1, "decreasing": 2, "unknown": 3}
    return (
        closure["province"].casefold(),
        tuple(value.casefold() for value in closure["localities"]),
        _natural_road_key(closure["road"]),
        _km_sort_key(closure["km_start"]),
        _km_sort_key(closure["km_end"]),
        direction_rank[closure["direction"]],
        closure["reason"].casefold(),
        tuple(closure["source_ids"]),
    )


def _natural_road_key(road: str) -> tuple[Any, ...]:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", road)
    )


def _normalise_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int, float)):
        values = [value]
    else:
        try:
            values = list(value)
        except TypeError:
            values = [value]
    cleaned = {_clean_text(item) for item in values}
    cleaned.discard("")
    return sorted(cleaned, key=str.casefold)


def _normalise_localities(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    else:
        try:
            values = list(value)
        except TypeError:
            values = [value]
    cleaned = {_clean_text(item) for item in values}
    cleaned.discard("")
    return sorted(cleaned, key=str.casefold)


def _normalise_direction(value: Any) -> str:
    cleaned = _clean_text(value).casefold()
    return _DIRECTION_ALIASES.get(cleaned, "unknown")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _normalise_km(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    # DATEX values are numeric.  Supporting decimal comma here also makes
    # hand-built fixtures and migrated state canonical.
    decimal_value = _parse_decimal(text)
    if decimal_value is None:
        return text
    normalised = format(decimal_value.normalize(), "f")
    return "0" if normalised in {"-0", ""} else normalised


def _normalise_published_at(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    parsed = _parse_published_at(text)
    if parsed is None:
        return ""
    # Canonicalising avoids false updates when DATEX alternates equivalent
    # forms such as a trailing Z and +00:00 or optional zero microseconds.
    return parsed.isoformat(timespec="seconds")


def _parse_published_at(value: str) -> datetime | None:
    candidate = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    # DATEX creation times are required to carry their own zone.  A naive
    # timestamp cannot fulfil the display contract, so use the explicit
    # fallback instead of guessing the runner's timezone.
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _parse_decimal(text: str) -> Decimal | None:
    candidate = text.strip().replace(" ", "")
    if "," in candidate and "." in candidate:
        if candidate.rfind(",") > candidate.rfind("."):
            candidate = candidate.replace(".", "").replace(",", ".")
        else:
            candidate = candidate.replace(",", "")
    else:
        candidate = candidate.replace(",", ".")
    try:
        return Decimal(candidate)
    except InvalidOperation:
        return None


def _km_decimal(value: str) -> Decimal | None:
    return _parse_decimal(value) if value else None


def _km_sort_key(value: str) -> tuple[int, Any]:
    number = _km_decimal(value)
    return (0, number) if number is not None else (1, value.casefold())


def _format_kilometres(start: str, end: str) -> str:
    if not start and not end:
        return "No indicados"
    if not start:
        return _format_single_km(end)
    if not end or start == end:
        return _format_single_km(start)
    return f"{_format_single_km(start)}–{_format_single_km(end)}"


def _format_single_km(value: str) -> str:
    number = _km_decimal(value)
    if number is None:
        return value
    # DGT road points conventionally use three decimal positions.
    return f"{number:.3f}".replace(".", ",")


def _format_published_at(value: str) -> str:
    parsed = _parse_published_at(value) if value else None
    if parsed is None:
        return "Fecha no indicada"
    # Do not convert to the GitHub runner's zone: the displayed wall time and
    # date intentionally come from the offset carried by the DGT timestamp.
    return parsed.strftime("%d/%m/%Y · %H:%M h")

