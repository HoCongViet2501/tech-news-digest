"""Viblo (Vietnamese developer community) trending posts."""

import logging
from datetime import datetime

import httpx

from digest.fetchers.text import excerpt
from digest.http import get_with_retry
from digest.models import Item, item_id

API = "https://viblo.asia/api/posts/trending"
SOURCE = "viblo"

log = logging.getLogger(__name__)


async def fetch(client: httpx.AsyncClient, *, now: datetime) -> list[Item]:
    """`now` is unused but kept for the common fetcher signature."""
    try:
        response = await get_with_retry(client, API)
        return [_to_item(post) for post in response.json()["data"] if post.get("url")]
    except Exception as exc:  # fetchers never raise
        log.warning("viblo: fetch failed: %s", exc)
        return []


def _to_item(post: dict) -> Item:
    tags = (post.get("tags") or {}).get("data") or []
    return Item(
        id=item_id(post["url"]),
        source=SOURCE,
        title=post["title"],
        url=post["url"],
        score=post.get("points") or 0,  # net votes; views_count is also available
        comments=post.get("comments_count") or 0,
        published_at=datetime.fromisoformat(post["published_at"]),
        tags=[tag["slug"] for tag in tags if tag.get("slug")],
        description=excerpt(post.get("contents_short")),
    )
