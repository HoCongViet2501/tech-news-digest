"""RSS/Atom feeds; no native score, so each feed has a fixed base score.

`fetch` serves the Telegram digest (feeds from config.yaml). `fetch_feed` serves the
content store (feeds from sources.yaml) and supports conditional GET.
"""

import calendar
import logging
from datetime import UTC, datetime, timedelta

import feedparser
import httpx
from pydantic import BaseModel, Field

from digest.config import Config, RssFeedConfig
from digest.fetchers.text import excerpt
from digest.http import conditional_get, get_with_retry
from digest.models import Item, item_id

MAX_AGE = timedelta(hours=36)

log = logging.getLogger(__name__)


class FeedResult(BaseModel):
    """Items of one feed plus the validators to store for the next conditional GET."""

    items: list[Item] = Field(default_factory=list)
    not_modified: bool = False  # 304: nothing to do, items is empty
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None  # set when the fetch failed; validators are then the old ones


async def fetch(cfg: Config, client: httpx.AsyncClient, now: datetime) -> list[Item]:
    items: list[Item] = []
    for feed in cfg.sources.rss:
        items.extend(await _fetch_feed(feed, client, now))
    return items


async def _fetch_feed(feed: RssFeedConfig, client: httpx.AsyncClient, now: datetime) -> list[Item]:
    try:
        response = await get_with_retry(client, feed.url)
        return parse_feed(
            response.content, source=f"rss:{feed.name}", score=feed.base_score, now=now
        )
    except Exception as exc:  # fetchers never raise
        log.warning("rss:%s: fetch failed: %s", feed.name, exc)
        return []


async def fetch_feed(
    client: httpx.AsyncClient,
    *,
    name: str,
    url: str,
    base_weight: int,
    now: datetime,
    etag: str | None = None,
    last_modified: str | None = None,
    max_age: timedelta | None = None,
) -> FeedResult:
    """Fetch one feed with conditional GET. Never raises: errors land in FeedResult.error.

    `base_weight` becomes Item.score. `max_age` drops older entries; None keeps all.
    """
    source = f"rss:{name}"
    try:
        response = await conditional_get(client, url, etag=etag, last_modified=last_modified)
        if response.not_modified:
            return FeedResult(
                not_modified=True, etag=response.etag, last_modified=response.last_modified
            )
        items = parse_feed(
            response.body, source=source, score=base_weight, now=now, max_age=max_age
        )
        return FeedResult(items=items, etag=response.etag, last_modified=response.last_modified)
    except Exception as exc:  # fetchers never raise
        log.warning("%s: fetch failed: %s", source, exc)
        return FeedResult(etag=etag, last_modified=last_modified, error=str(exc) or repr(exc))


def parse_feed(
    content: bytes,
    *,
    source: str,
    score: int,
    now: datetime,
    max_age: timedelta | None = MAX_AGE,
) -> list[Item]:
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"not a feed: {parsed.get('bozo_exception')}")
    items = []
    for entry in parsed.entries:
        if not entry.get("link") or not entry.get("title"):
            continue
        published = _published(entry) or now
        if max_age is not None and published < now - max_age:
            continue
        items.append(
            Item(
                id=item_id(entry.link),
                source=source,
                title=entry.title,
                url=entry.link,
                score=score,
                published_at=published,
                description=excerpt(entry.get("summary")),
            )
        )
    return items


def _published(entry: feedparser.FeedParserDict) -> datetime | None:
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(struct), UTC) if struct else None
