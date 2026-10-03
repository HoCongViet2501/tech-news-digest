"""Collect button taps with getUpdates polling; there is no server to receive them.

Never set a webhook on this bot: getUpdates stops working while one is set.
The token is part of the URL, so errors are built from status codes only.
"""

import json
import logging
import re
from collections.abc import Iterable
from datetime import datetime

import httpx

from digest.models import ScoredItem, Vote

GET_UPDATES = "https://api.telegram.org/bot{token}/getUpdates"
CALLBACK = re.compile(r"^fb:([0-9a-f]{8}):(up|down)$")

log = logging.getLogger(__name__)


class FeedbackError(Exception):
    """Telegram could not be reached or rejected the request."""


async def get_updates(client: httpx.AsyncClient, token: str, offset: int | None) -> list[dict]:
    """Pending updates; passing `offset` also confirms (deletes) every earlier update."""
    params: dict[str, str | int] = {
        "timeout": 0,
        "allowed_updates": json.dumps(["callback_query"]),
    }
    if offset is not None:
        params["offset"] = offset
    try:
        response = await client.get(GET_UPDATES.format(token=token), params=params)
    except httpx.HTTPError as exc:
        raise FeedbackError(f"{type(exc).__name__} reaching Telegram") from None
    if response.status_code != 200:
        try:
            reason = str(response.json().get("description", "no description"))
        except ValueError:
            reason = "non-JSON response"
        raise FeedbackError(f"HTTP {response.status_code}: {reason}")
    return list(response.json().get("result") or [])


def next_offset(updates: list[dict], current: int | None) -> int | None:
    ids = [u["update_id"] for u in updates if isinstance(u.get("update_id"), int)]
    return max(ids) + 1 if ids else current


def items_by_prefix(items: Iterable[ScoredItem]) -> dict[str, ScoredItem]:
    """First 8 chars of the id (as in callback_data) -> item; later digests win."""
    return {item.id[:8]: item for item in items}


def _same_chat(chat: dict, chat_id: str) -> bool:
    """TELEGRAM_CHAT_ID may be a numeric id or a public "@channelname"."""
    if str(chat.get("id")) == str(chat_id):
        return True
    username = chat.get("username")
    return bool(username) and f"@{username}".lower() == str(chat_id).lower()


def parse_votes(
    updates: list[dict],
    chat_id: str,
    items: dict[str, ScoredItem],
    known_updates: set[int],
    now: datetime,
) -> list[Vote]:
    votes = []
    for update in updates:
        query = update.get("callback_query")
        update_id = update.get("update_id")
        if not query or not isinstance(update_id, int) or update_id in known_updates:
            continue
        if not _same_chat((query.get("message") or {}).get("chat") or {}, chat_id):
            log.info("feedback: ignoring a tap from another chat")
            continue
        match = CALLBACK.match(str(query.get("data") or ""))
        if not match:
            continue
        item = items.get(match.group(1))
        if item is None:
            log.info("feedback: no saved item for %s, skipping", match.group(1))
            continue
        votes.append(
            Vote(
                ts=now,
                update_id=update_id,
                item_id=item.id,
                vote="up" if match.group(2) == "up" else "down",
                title=item.title,
                source=item.source,
            )
        )
    return votes
