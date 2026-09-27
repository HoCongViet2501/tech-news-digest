"""Hacker News stories from the last 24 hours via the Algolia API."""

import logging
from datetime import UTC, datetime, timedelta

import httpx

from digest.config import Config
from digest.http import get_with_retry
from digest.models import Item, item_id

API = "https://hn.algolia.com/api/v1/search_by_date"
DISCUSSION = "https://news.ycombinator.com/item?id={}"

log = logging.getLogger(__name__)


async def fetch(cfg: Config, client: httpx.AsyncClient, now: datetime) -> list[Item]:
    hn = cfg.sources.hackernews
    since = int((now - timedelta(hours=24)).timestamp())
    params = {
        "tags": "story",
        # The real min_points threshold is applied in filter.py so that
        # include-keyword stories below it can still be kept.
        "numericFilters": f"created_at_i>{since},points>{hn.fetch_min_points}",
        "hitsPerPage": 100,
    }
    try:
        response = await get_with_retry(client, API, params=params)
        return [_to_item(hit) for hit in response.json()["hits"] if hit.get("title")]
    except Exception as exc:  # fetchers never raise
        log.warning("hackernews: fetch failed: %s", exc)
        return []


def _to_item(hit: dict) -> Item:
    discussion = DISCUSSION.format(hit["objectID"])
    url = hit.get("url") or discussion
    return Item(
        id=item_id(url),
        source="hn",
        title=hit["title"],
        url=url,
        discussion_url=discussion,
        score=hit.get("points") or 0,
        comments=hit.get("num_comments") or 0,
        published_at=datetime.fromtimestamp(hit["created_at_i"], UTC),
    )
