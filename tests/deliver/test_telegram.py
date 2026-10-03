import asyncio
import json
import logging

import httpx
import pytest
import respx

from digest.deliver.telegram import DeliveryError, send_messages
from digest.http import make_client
from digest.models import TelegramMessage

TOKEN = "123456:SECRET-token-value"
URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"


def send(messages: list[str | TelegramMessage]) -> None:
    wrapped = [m if isinstance(m, TelegramMessage) else TelegramMessage(text=m) for m in messages]

    async def go() -> None:
        async with make_client() as client:
            await send_messages(client, TOKEN, "42", wrapped)

    asyncio.run(go())


@respx.mock
def test_sends_each_message_in_order_as_html_without_previews() -> None:
    route = respx.post(URL).respond(200, json={"ok": True, "result": {}})

    send(["<b>one</b>", "two"])

    bodies = [json.loads(call.request.content) for call in route.calls]
    assert [b["text"] for b in bodies] == ["<b>one</b>", "two"]
    assert all(b["chat_id"] == "42" for b in bodies)
    assert all(b["parse_mode"] == "HTML" for b in bodies)
    assert all(b["link_preview_options"] == {"is_disabled": True} for b in bodies)


@respx.mock
def test_api_error_raises_with_telegram_description_and_stops() -> None:
    route = respx.post(URL).respond(
        400, json={"ok": False, "description": "Bad Request: can't parse entities"}
    )

    with pytest.raises(DeliveryError, match="can't parse entities") as exc:
        send(["first", "second"])

    assert route.call_count == 1
    assert TOKEN not in str(exc.value)


@respx.mock
def test_network_error_does_not_leak_token(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    respx.post(URL).mock(side_effect=httpx.ConnectError(f"cannot reach {URL}"))

    with pytest.raises(DeliveryError) as exc:
        send(["x"])

    assert TOKEN not in str(exc.value)
    assert TOKEN not in caplog.text


@respx.mock
def test_success_does_not_log_token(caplog: pytest.LogCaptureFixture) -> None:
    from digest.logs import setup_logging

    setup_logging()
    caplog.set_level(logging.INFO)
    respx.post(URL).respond(200, json={"ok": True, "result": {}})

    send(["x"])

    assert TOKEN not in caplog.text


@respx.mock
def test_feedback_buttons_one_row_per_item() -> None:
    route = respx.post(URL).respond(200, json={"ok": True, "result": {}})
    item_id = "e30f8dee" + "0" * 32

    send(
        [
            TelegramMessage(text="digest", buttons=[(1, item_id), (2, "ab12cd34" + "f" * 32)]),
            "plain",
        ]
    )

    with_buttons, plain = [json.loads(call.request.content) for call in route.calls]
    rows = with_buttons["reply_markup"]["inline_keyboard"]
    assert rows[0] == [
        {"text": "👍 1", "callback_data": "fb:e30f8dee:up"},
        {"text": "👎 1", "callback_data": "fb:e30f8dee:down"},
    ]
    assert rows[1][1]["callback_data"] == "fb:ab12cd34:down"
    assert all(len(b["callback_data"].encode()) <= 64 for row in rows for b in row)
    assert "reply_markup" not in plain
