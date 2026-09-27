"""RSS/Atom feeds from config; no native score, so each feed has a base_score."""

import calendar
import logging
from datetime import UTC, datetime, timedelta

import feedparser
import httpx

from digest.config import Config, RssFeedConfig
from digest.http import get_with_retry
from digest.models import Item, item_id

MAX_AGE = timedelta(hours=36)

log = logging.getLogger(__name__)


async def fetch(cfg: Config, client: httpx.AsyncClient, now: datetime) -> list[Item]:
    items: list[Item] = []
    for feed in cfg.sources.rss:
        items.extend(await _fetch_feed(feed, client, now))
    return items


async def _fetch_feed(feed: RssFeedConfig, client: httpx.AsyncClient, now: datetime) -> list[Item]:
    try:
        response = await get_with_retry(client, feed.url)
        parsed = feedparser.parse(response.content)
        items = []
        for entry in parsed.entries:
            if not entry.get("link") or not entry.get("title"):
                continue
            published = _published(entry) or now
            if published < now - MAX_AGE:
                continue
            items.append(
                Item(
                    id=item_id(entry.link),
                    source=f"rss:{feed.name}",
                    title=entry.title,
                    url=entry.link,
                    score=feed.base_score,
                    published_at=published,
                )
            )
        return items
    except Exception as exc:  # fetchers never raise
        log.warning("rss:%s: fetch failed: %s", feed.name, exc)
        return []


def _published(entry: feedparser.FeedParserDict) -> datetime | None:
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(struct), UTC) if struct else None
