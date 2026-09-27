"""Lobsters hottest stories."""

import logging
from datetime import datetime

import httpx

from digest.config import Config
from digest.http import get_with_retry
from digest.models import Item, item_id

API = "https://lobste.rs/hottest.json"

log = logging.getLogger(__name__)


async def fetch(cfg: Config, client: httpx.AsyncClient, now: datetime) -> list[Item]:
    try:
        response = await get_with_retry(client, API)
        return [_to_item(story) for story in response.json()]
    except Exception as exc:  # fetchers never raise
        log.warning("lobsters: fetch failed: %s", exc)
        return []


def _to_item(story: dict) -> Item:
    url = story.get("url") or story["comments_url"]
    return Item(
        id=item_id(url),
        source="lobsters",
        title=story["title"],
        url=url,
        discussion_url=story["comments_url"],
        score=story.get("score") or 0,
        comments=story.get("comment_count") or 0,
        published_at=datetime.fromisoformat(story["created_at"]),
        tags=list(story.get("tags") or []),
    )
