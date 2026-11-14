"""dev.to articles: the daily top list plus the weekly top list of each followed tag."""

import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime

import httpx

from digest.fetchers.text import excerpt
from digest.http import get_with_retry
from digest.models import Item, item_id

API = "https://dev.to/api/articles"
SOURCE = "devto"

log = logging.getLogger(__name__)


async def fetch(
    client: httpx.AsyncClient,
    *,
    tags: Sequence[str] = (),
    now: datetime,
    per_page: int = 30,
    top_days: int = 1,
    tag_top_days: int = 7,
    pause_seconds: float = 1.0,
) -> list[Item]:
    """Top articles of the last `top_days`, then of the last `tag_top_days` for each tag.

    An article listed under several tags is returned once. `now` is unused (the API
    gives publish times) but kept for the common fetcher signature. dev.to answers 429
    to quick bursts, so requests are spaced by `pause_seconds`.
    """
    requests = [{"top": top_days, "per_page": per_page}]
    requests += [{"tag": tag, "top": tag_top_days, "per_page": per_page} for tag in tags]
    items: dict[str, Item] = {}
    for n, params in enumerate(requests):
        if n and pause_seconds:
            await asyncio.sleep(pause_seconds)
        for item in await _fetch_page(client, params):
            items.setdefault(item.id, item)
    return list(items.values())


async def _fetch_page(client: httpx.AsyncClient, params: dict[str, int | str]) -> list[Item]:
    label = params.get("tag", "top")
    try:
        response = await get_with_retry(client, API, params=params)
        return [_to_item(article) for article in response.json() if article.get("title")]
    except Exception as exc:  # fetchers never raise
        log.warning("devto: %s failed: %s", label, exc)
        return []


def _to_item(article: dict) -> Item:
    page = article["url"]
    # Cross-posts point canonical_url at the original blog; keep dev.to as the discussion.
    url = article.get("canonical_url") or page
    return Item(
        id=item_id(url),
        source=SOURCE,
        title=article["title"],
        url=url,
        discussion_url=page if url != page else None,
        score=article.get("public_reactions_count") or 0,
        comments=article.get("comments_count") or 0,
        published_at=datetime.fromisoformat(article["published_timestamp"]),
        tags=list(article.get("tag_list") or []),
        description=excerpt(article.get("description")),
    )
