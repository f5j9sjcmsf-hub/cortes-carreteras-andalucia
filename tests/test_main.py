from copy import deepcopy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import logic
import main


NOW = "2026-08-17T22:00:00+02:00"


def closure():
    return {
        "source_ids": ["s1:r1"],
        "situation_ids": ["s1"],
        "record_ids": ["r1"],
        "province": "Granada",
        "localities": ["Güéjar Sierra"],
        "road": "A-395",
        "km_start": 31,
        "km_end": 39,
        "direction": "both",
        "reason": "Desprendimientos",
        "cause_code": "environmentalObstruction",
        "detail_code": "rockfalls",
    }


@patch("main._now_iso", return_value=NOW)
@patch("main.save_state")
@patch("main.send_messages")
@patch("main.fetch_closures")
@patch("main.load_state", return_value={})
@patch("main.validate_configuration")
def test_first_run_sends_summary_and_complete_list_before_saving(
    validate,
    load,
    fetch,
    send,
    save,
    now,
):
    fetch.return_value = [closure()]
    calls = []
    send.side_effect = lambda _messages: calls.append("send")
    save.side_effect = lambda _state: calls.append("save")

    main.main()

    validate.assert_called_once_with()
    fetch.assert_called_once_with(url=main.DGT_URL, timeout=main.DGT_TIMEOUT_SECONDS)
    outgoing = send.call_args.args[0]
    assert len(outgoing) == 2
    assert "ESTADO INICIAL" in outgoing[0]
    assert "1 corte activo" in outgoing[0]
    assert "CARRETERA CORTADA" in outgoing[1]
    assert calls == ["send", "save"]
    saved = save.call_args.args[0]
    assert saved["initialized"] is True
    assert len(saved["active"]) == 1


@patch("main._now_iso", return_value=NOW)
@patch("main.save_state")
@patch("main.send_messages")
@patch("main.fetch_closures", return_value=[])
@patch("main.load_state", return_value={})
@patch("main.validate_configuration")
def test_empty_first_snapshot_still_confirms_that_monitoring_started(
    _validate,
    _load,
    _fetch,
    send,
    save,
    _now,
):
    main.main()

    outgoing = send.call_args.args[0]
    assert len(outgoing) == 1
    assert "0 cortes activos" in outgoing[0]
    assert save.call_args.args[0]["active"] == {}


@patch("main._now_iso", return_value=NOW)
@patch("main.save_state")
@patch("main.send_messages")
@patch("main.fetch_closures")
@patch("main.load_state")
@patch("main.validate_configuration")
def test_unchanged_poll_is_silent_but_updates_operational_state(
    _validate,
    load,
    fetch,
    send,
    save,
    _now,
):
    _, state = logic.plan_initial({}, [closure()], "2026-08-17T21:45:00+02:00")
    load.return_value = deepcopy(state)
    fetch.return_value = [closure()]

    main.main()

    send.assert_called_once_with([])
    assert save.call_args.args[0]["revision"] == state["revision"] + 1


@patch("main._now_iso", return_value=NOW)
@patch("main.save_state")
@patch("main.send_messages", side_effect=RuntimeError("Telegram caído"))
@patch("main.fetch_closures", return_value=[closure()])
@patch("main.load_state", return_value={})
@patch("main.validate_configuration")
def test_telegram_failure_never_advances_state(
    _validate,
    _load,
    _fetch,
    _send,
    save,
    _now,
):
    with pytest.raises(RuntimeError, match="Telegram caído"):
        main.main()
    save.assert_not_called()


@patch("main.save_state")
@patch("main.send_messages")
@patch("main.fetch_closures", side_effect=RuntimeError("DGT caída"))
@patch("main.load_state", return_value={})
@patch("main.validate_configuration")
def test_source_failure_never_sends_or_advances_state(
    _validate,
    _load,
    _fetch,
    send,
    save,
):
    with pytest.raises(RuntimeError, match="DGT caída"):
        main.main()
    send.assert_not_called()
    save.assert_not_called()

