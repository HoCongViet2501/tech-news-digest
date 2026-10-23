"""Send rendered messages through the Telegram Bot API.

The token is part of the request URL, so error messages are built from the
status code and Telegram's description only, never from httpx exceptions.
"""

import logging

import httpx

from digest.models import TelegramMessage

API = "https://api.telegram.org/bot{token}/sendMessage"

log = logging.getLogger(__name__)


class DeliveryError(Exception):
    """Telegram rejected a message or could not be reached."""


def feedback_keyboard(buttons: list[tuple[int, str]]) -> dict:
    """One like/dislike row per item; callback_data `fb:<id8>:<up|down>` fits the 64-byte cap."""
    return {
        "inline_keyboard": [
            [
                {"text": f"👍 {n}", "callback_data": f"fb:{item_id[:8]}:up"},
                {"text": f"👎 {n}", "callback_data": f"fb:{item_id[:8]}:down"},
            ]
            for n, item_id in buttons
        ]
    }


async def send_messages(
    client: httpx.AsyncClient, token: str, chat_id: str, messages: list[TelegramMessage]
) -> None:
    url = API.format(token=token)
    for n, message in enumerate(messages, 1):
        payload: dict = {
            "chat_id": chat_id,
            "text": message.text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if message.buttons:
            payload["reply_markup"] = feedback_keyboard(message.buttons)
        try:
            response = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise DeliveryError(
                f"message {n}/{len(messages)}: {type(exc).__name__} reaching Telegram"
            ) from None
        if response.status_code != 200:
            reason = _description(response)
            raise DeliveryError(
                f"message {n}/{len(messages)}: HTTP {response.status_code}: {reason}"
            )
        log.info("telegram: sent message %d/%d", n, len(messages))


def _description(response: httpx.Response) -> str:
    try:
        return str(response.json().get("description", "no description"))
    except ValueError:
        return "non-JSON response"
