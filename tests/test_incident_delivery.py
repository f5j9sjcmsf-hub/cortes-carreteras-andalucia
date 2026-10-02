import sys
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import incident_delivery
from incidents import append_event, format_active_incident, format_final_summary, new_incident
from logic import EVENT_REOPENED, EVENT_UPDATED, reconcile_events


T0 = "2026-10-02T14:00:00+02:00"
T1 = "2026-10-02T14:25:00+02:00"
T2 = "2026-10-02T15:00:00+02:00"


def closure(**overrides):
    item = {
        "source_ids": ["source-1"],
        "situation_ids": ["situation-1"],
        "record_ids": ["record-1"],
        "province": "Jaén",
        "localities": ["Cárcheles", "Pegalajar"],
        "road": "A-44",
        "km_start": 55.8,
        "km_end": 59.7,
        "direction": "decreasing",
        "reason": "Obras",
        "cause_code": "roadMaintenance",
        "detail_code": "roadworks",
        "published_at": T0,
        "source_updated_at": T0,
        "alternative": "",
    }
    item.update(overrides)
    return item


def forum(_province):
    return {"chat_id": "-100200", "message_thread_id": 44}


def result(message_id):
    return {"message_id": message_id}


def test_history_is_added_to_the_primary_and_final_summary():
    incident = new_incident("key", closure(), T0)
    incident = append_event(
        incident,
        EVENT_UPDATED,
        closure(alternative="Carril reversible", source_updated_at=T1),
        T1,
    )
    active = format_active_incident(incident)
    incident = append_event(incident, EVENT_REOPENED, closure(), T2)
    final = format_final_summary(incident, T2)

    assert "Historial de la incidencia" in active
    assert "Alternativa: Carril reversible" in active
    assert "CORTE FINALIZADO" in final
    assert "Carretera reabierta" in final
    assert "Duración total: 1 hora" in final


@patch.object(incident_delivery, "TELEGRAM_CHAT_ID", "@canal")
@patch("incident_delivery.forum_destination", side_effect=forum)
@patch("incident_delivery.edit_message")
@patch("incident_delivery.delete_message")
@patch("incident_delivery.send_message")
def test_update_is_published_as_a_reply_and_retained_in_primary(
    send, delete, edit, _forum
):
    send.side_effect = [result(101), result(201)]
    events, state = reconcile_events({}, [closure()], T0)
    state = incident_delivery.process_incident_events({}, state, events, T0)

    send.reset_mock()
    send.side_effect = [result(102), result(202)]
    updated = closure(alternative="Carril reversible", source_updated_at=T1)
    events, next_state = reconcile_events(state, [updated], T1)
    next_state = incident_delivery.process_incident_events(state, next_state, events, T1)

    assert edit.call_count == 2
    assert send.call_count == 2
    assert {call.kwargs["reply_to_message_id"] for call in send.call_args_list} == {101, 201}
    incident = next(iter(next_state["telegram_incidents"].values()))
    assert len(incident["alerts"]) == 1
    assert "Carril reversible" in format_active_incident(incident)
    delete.assert_not_called()


@patch.object(incident_delivery, "TELEGRAM_CHAT_ID", "@canal")
@patch("incident_delivery.forum_destination", side_effect=forum)
@patch("incident_delivery.edit_message")
@patch("incident_delivery.delete_message")
@patch("incident_delivery.send_message")
def test_update_notice_expires_but_information_remains_in_primary(
    send, delete, _edit, _forum
):
    send.side_effect = [result(101), result(201)]
    events, state = reconcile_events({}, [closure()], T0)
    state = incident_delivery.process_incident_events({}, state, events, T0)
    send.side_effect = [result(102), result(202)]
    updated = closure(alternative="Carril reversible", source_updated_at=T1)
    events, next_state = reconcile_events(state, [updated], T1)
    state = incident_delivery.process_incident_events(state, next_state, events, T1)

    later = "2026-10-04T03:00:00+02:00"
    events, next_state = reconcile_events(state, [updated], later)
    state = incident_delivery.process_incident_events(state, next_state, events, later)

    assert {call.args[1] for call in delete.call_args_list} == {102, 202}
    incident = next(iter(state["telegram_incidents"].values()))
    assert incident["alerts"] == []
    assert "Carril reversible" in format_active_incident(incident)


@patch.object(incident_delivery, "TELEGRAM_CHAT_ID", "@canal")
@patch("incident_delivery.forum_destination", side_effect=forum)
@patch("incident_delivery.edit_message")
@patch("incident_delivery.delete_message")
@patch("incident_delivery.send_message")
def test_short_incident_replaces_live_messages_with_one_final_summary_each(
    send, delete, _edit, _forum
):
    send.side_effect = [result(101), result(201)]
    events, state = reconcile_events({}, [closure()], T0)
    state = incident_delivery.process_incident_events({}, state, events, T0)
    send.reset_mock()
    send.side_effect = [result(103), result(203)]

    events, next_state = reconcile_events(state, [], T2)
    final_state = incident_delivery.process_incident_events(state, next_state, events, T2)

    assert {call.args[1] for call in delete.call_args_list} == {101, 201}
    assert send.call_count == 2
    assert all("CORTE FINALIZADO" in call.args[0] for call in send.call_args_list)
    assert final_state["telegram_incidents"] == {}
    assert len(final_state["telegram_archive"]) == 1
    assert "Duración total: 1 hora" in final_state["telegram_archive"][0]["final_summary"]

