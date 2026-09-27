"""Run all enabled fetchers concurrently; one failing source never affects the others."""

import asyncio
import logging
from datetime import datetime

import httpx

from digest.config import Config
from digest.fetchers import github_trending, hackernews, lobsters, rss
from digest.fetchers.base import FetchFn
from digest.models import Item

FETCHERS: dict[str, FetchFn] = {
    "hn": hackernews.fetch,
    "github": github_trending.fetch,
    "lobsters": lobsters.fetch,
    "rss": rss.fetch,
}

log = logging.getLogger(__name__)


def _enabled(cfg: Config, name: str) -> bool:
    match name:
        case "hn":
            return cfg.sources.hackernews.enabled
        case "github":
            return cfg.sources.github_trending.enabled
        case "lobsters":
            return cfg.sources.lobsters.enabled
        case "reddit":
            return cfg.sources.reddit.enabled
        case _:
            return True


async def _safe(name: str, fn: FetchFn, cfg: Config, client: httpx.AsyncClient, now: datetime):
    try:
        return await fn(cfg, client, now)
    except Exception:
        log.exception("%s: fetcher raised unexpectedly", name)
        return []


async def fetch_all(
    cfg: Config, client: httpx.AsyncClient, now: datetime, only: list[str] | None = None
) -> dict[str, list[Item]]:
    names = [n for n in (only or FETCHERS) if _enabled(cfg, n)]
    for name in names:
        if name not in FETCHERS:
            log.warning("%s: no fetcher implemented yet, skipping", name)
    names = [n for n in names if n in FETCHERS]
    results = await asyncio.gather(*(_safe(n, FETCHERS[n], cfg, client, now) for n in names))
    return dict(zip(names, results, strict=True))
