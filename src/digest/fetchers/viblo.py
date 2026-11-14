"""Viblo (Vietnamese developer community): editors' choice plus the newest posts."""

import logging
from datetime import datetime

import httpx

from digest.fetchers.text import excerpt
from digest.http import get_with_retry
from digest.models import Item, item_id

API = "https://viblo.asia/api/posts/{feed}"
FEEDS = ("editors-choice", "newest")
SOURCE = "viblo"

log = logging.getLogger(__name__)


async def fetch(client: httpx.AsyncClient, *, now: datetime, limit: int = 20) -> list[Item]:
    """`limit` posts from each feed; a post in both is returned once (editors' choice
    first). `now` is unused but kept for the common fetcher signature."""
    items: dict[str, Item] = {}
    for feed in FEEDS:
        for item in await _fetch_feed(client, feed, limit):
            items.setdefault(item.id, item)
    return list(items.values())


async def _fetch_feed(client: httpx.AsyncClient, feed: str, limit: int) -> list[Item]:
    try:
        response = await get_with_retry(client, API.format(feed=feed), params={"limit": limit})
        return [_to_item(post) for post in response.json()["data"] if post.get("url")]
    except Exception as exc:  # fetchers never raise
        log.warning("viblo: %s failed: %s", feed, exc)
        return []


def _to_item(post: dict) -> Item:
    tags = (post.get("tags") or {}).get("data") or []
    return Item(
        id=item_id(post["url"]),
        source=SOURCE,
        title=post["title"],
        url=post["url"],
        # Votes are near zero on most posts; views separate them far better.
        score=post.get("views_count") or 0,
        comments=post.get("comments_count") or 0,
        published_at=datetime.fromisoformat(post["published_at"]),
        tags=[tag["slug"] for tag in tags if tag.get("slug")],
        description=excerpt(post.get("contents_short")),
    )
