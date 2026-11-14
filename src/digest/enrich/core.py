"""Fetch an article page politely and turn it into an Enrichment. Never raises."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Iterable
from urllib.parse import urlsplit

import httpx

from digest.enrich.model import Enrichment
from digest.enrich.page import decode_html, parse_page
from digest.enrich.robots import RobotsCache

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 2 * 1024 * 1024
HTML_TYPES = ("text/html", "application/xhtml+xml")


class Skip(Exception):
    """Page not usable; the message is recorded as Enrichment.skip_reason."""


async def _download(client: httpx.AsyncClient, url: str, timeout_s: float) -> tuple[str, str]:
    """GET an HTML page, reading at most MAX_BODY_BYTES. Returns (html, final url)."""
    headers = {"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"}
    async with client.stream("GET", url, headers=headers, timeout=timeout_s) as response:
        if response.status_code >= 400:
            raise Skip(f"http {response.status_code}")
        content_type = response.headers.get("content-type", "")
        if content_type.split(";", 1)[0].strip().lower() not in HTML_TYPES:
            raise Skip(f"content-type {content_type or 'missing'}")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) >= MAX_BODY_BYTES:
                log.info("%s is larger than %d bytes; parsing the first part", url, MAX_BODY_BYTES)
                del body[MAX_BODY_BYTES:]
                break
        return decode_html(bytes(body), content_type), str(response.url)


async def enrich(
    client: httpx.AsyncClient, url: str, *, timeout_s: float, robots: RobotsCache
) -> Enrichment:
    """og:image, description, reading time and language of one page. On any failure returns
    an empty Enrichment whose skip_reason says why, and logs at info level."""
    try:
        reason = await robots.check(client, url, timeout_s=timeout_s)
        if reason:
            log.info("enrich skipped %s: %s", url, reason)
            return Enrichment(skip_reason=reason)
        async with asyncio.timeout(timeout_s):
            html, final_url = await _download(client, url, timeout_s)
        # trafilatura and lxml are CPU-bound; keep the event loop free for other downloads.
        return await asyncio.to_thread(parse_page, html, final_url)
    except Skip as exc:
        reason = str(exc)
    except TimeoutError:
        reason = "timeout"
    except Exception as exc:
        reason = type(exc).__name__
    log.info("enrich failed for %s: %s", url, reason)
    return Enrichment(skip_reason=reason)


async def enrich_many(
    client: httpx.AsyncClient,
    urls: Iterable[str],
    *,
    concurrency: int = 8,
    per_host: int = 2,
    timeout_s: float = 10,
    max_items: int,
    robots: RobotsCache | None = None,
) -> dict[str, Enrichment]:
    """Enrich up to `max_items` distinct URLs (config: ingest.enrich_per_run), at most
    `concurrency` in flight overall and `per_host` per host."""
    todo = list(dict.fromkeys(urls))[: max(0, max_items)]
    robots = robots or RobotsCache()
    overall = asyncio.Semaphore(concurrency)
    hosts: defaultdict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(per_host))

    async def one(url: str) -> tuple[str, Enrichment]:
        # Take the host slot first so a busy host never holds global slots while it waits.
        async with hosts[urlsplit(url).netloc.lower()], overall:
            return url, await enrich(client, url, timeout_s=timeout_s, robots=robots)

    results = await asyncio.gather(*(one(url) for url in todo))
    return dict(results)
