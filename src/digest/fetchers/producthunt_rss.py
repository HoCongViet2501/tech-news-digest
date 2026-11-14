"""Product Hunt Atom feed. Votes need a GraphQL token, so there is no score (always 0)."""

import logging
from datetime import datetime

import httpx

from digest.fetchers.rss import parse_feed
from digest.http import get_with_retry
from digest.models import Item

FEED = "https://www.producthunt.com/feed"
SOURCE = "producthunt"

log = logging.getLogger(__name__)


async def fetch(client: httpx.AsyncClient, *, now: datetime) -> list[Item]:
    """Every entry in the feed; `now` stands in for a missing publish date."""
    try:
        response = await get_with_retry(client, FEED)
        items = parse_feed(response.content, source=SOURCE, score=0, now=now, max_age=None)
    except Exception as exc:  # fetchers never raise
        log.warning("producthunt: fetch failed: %s", exc)
        return []
    return [item.model_copy(update={"description": _tagline(item.description)}) for item in items]


def _tagline(description: str | None) -> str | None:
    """Entries end with "Discussion | Link" anchors; keep only the tagline before them."""
    if description is None:
        return None
    return description.removesuffix("Discussion | Link").strip() or None
