"""Shared async HTTP client: timeout, retries on 5xx, custom User-Agent."""

import logging
from typing import Any

import httpx

USER_AGENT = "tech-news-digest/0.1 (+https://github.com/vietho-moso/tech-digest)"
TIMEOUT_SECONDS = 15
RETRIES = 2

log = logging.getLogger(__name__)


def make_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
        transport=transport,
    )


async def get_with_retry(client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
    """GET with up to RETRIES retries on 5xx or transport errors; 4xx raises immediately."""
    for attempt in range(RETRIES + 1):
        last = attempt == RETRIES
        try:
            response = await client.get(url, **kwargs)
        except httpx.TransportError as exc:
            if last:
                raise
            log.info("GET %s failed (%s), retrying", url, type(exc).__name__)
            continue
        if response.status_code >= 500 and not last:
            log.info("GET %s returned %d, retrying", url, response.status_code)
            continue
        response.raise_for_status()
        return response
    raise AssertionError("unreachable")
