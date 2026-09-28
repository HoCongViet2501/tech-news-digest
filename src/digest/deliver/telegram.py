"""Send rendered messages through the Telegram Bot API.

The token is part of the request URL, so error messages are built from the
status code and Telegram's description only, never from httpx exceptions.
"""

import logging

import httpx

API = "https://api.telegram.org/bot{token}/sendMessage"

log = logging.getLogger(__name__)


class DeliveryError(Exception):
    """Telegram rejected a message or could not be reached."""


async def send_messages(
    client: httpx.AsyncClient, token: str, chat_id: str, messages: list[str]
) -> None:
    url = API.format(token=token)
    for n, text in enumerate(messages, 1):
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
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
