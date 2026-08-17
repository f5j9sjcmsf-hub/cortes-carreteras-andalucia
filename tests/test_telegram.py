import sys
from pathlib import Path
from unittest.mock import Mock, call, patch

import pytest
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import telegram


def telegram_response(status=200, payload=None):
    response = Mock()
    response.status_code = status
    response.json.return_value = payload or {"ok": True}
    return response


@pytest.fixture
def configured_telegram():
    with (
        patch.object(telegram, "TELEGRAM_BOT_TOKEN", "123456:token-secreto"),
        patch.object(telegram, "TELEGRAM_CHAT_ID", "@canal_nuevo"),
    ):
        yield


def test_html_parse_mode_is_preserved(configured_telegram):
    with patch("telegram.requests.post", return_value=telegram_response()) as post:
        telegram.send_message("<b>Carretera cortada</b>")

    assert post.call_args.kwargs["data"]["parse_mode"] == "HTML"
    assert post.call_args.kwargs["data"]["text"] == "<b>Carretera cortada</b>"


def test_429_retries_only_the_current_message_and_honours_retry_after(
    configured_telegram,
):
    responses = [
        telegram_response(),
        telegram_response(
            429,
            {
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 4},
            },
        ),
        telegram_response(),
        telegram_response(),
    ]
    with (
        patch("telegram.requests.post", side_effect=responses) as post,
        patch("telegram.time.sleep") as sleep,
    ):
        telegram.send_messages(["primero", "segundo", "tercero"])

    sent_texts = [item.kwargs["data"]["text"] for item in post.call_args_list]
    assert sent_texts == ["primero", "segundo", "segundo", "tercero"]
    assert sleep.call_args_list == [
        call(telegram.MESSAGE_INTERVAL_SECONDS),
        call(4.0),
        call(telegram.MESSAGE_INTERVAL_SECONDS),
    ]


def test_rate_limit_has_a_bounded_number_of_retries(configured_telegram):
    limited = telegram_response(
        429,
        {
            "ok": False,
            "error_code": 429,
            "parameters": {"retry_after": 2},
        },
    )
    with (
        patch("telegram.requests.post", return_value=limited) as post,
        patch("telegram.time.sleep") as sleep,
        pytest.raises(RuntimeError, match="429.*5 reintentos"),
    ):
        telegram.send_message("mensaje")

    assert post.call_count == telegram.MAX_RATE_LIMIT_RETRIES + 1
    assert sleep.call_args_list == [
        call(2.0)
    ] * telegram.MAX_RATE_LIMIT_RETRIES


def test_initial_batch_of_27_messages_is_spaced_without_reordering(
    configured_telegram,
):
    messages = [f"corte-{index:02d}" for index in range(27)]
    with (
        patch("telegram.requests.post", return_value=telegram_response()) as post,
        patch("telegram.time.sleep") as sleep,
    ):
        telegram.send_messages(messages)

    assert [
        item.kwargs["data"]["text"] for item in post.call_args_list
    ] == messages
    assert sleep.call_args_list == [
        call(telegram.MESSAGE_INTERVAL_SECONDS)
    ] * 26


@pytest.mark.parametrize("status", [400, 403, 500, 503])
def test_other_http_errors_are_clear_and_never_expose_the_token(
    configured_telegram,
    status,
):
    secret = telegram.TELEGRAM_BOT_TOKEN
    response = telegram_response(
        status,
        {
            "ok": False,
            "error_code": status,
            "description": f"Fallo al usar bot{secret}/sendMessage",
        },
    )
    with patch("telegram.requests.post", return_value=response):
        with pytest.raises(RuntimeError) as raised:
            telegram.send_message("mensaje")

    diagnostic = str(raised.value)
    assert f"HTTP {status}" in diagnostic
    assert secret not in diagnostic
    assert "[oculto]" in diagnostic


def test_network_errors_are_sanitised(configured_telegram):
    secret = telegram.TELEGRAM_BOT_TOKEN
    network_error = requests.ConnectionError(
        f"fallo en https://api.telegram.org/bot{secret}/sendMessage"
    )
    with patch("telegram.requests.post", side_effect=network_error):
        with pytest.raises(RuntimeError) as raised:
            telegram.send_message("mensaje")

    assert "conectar con Telegram" in str(raised.value)
    assert secret not in str(raised.value)

