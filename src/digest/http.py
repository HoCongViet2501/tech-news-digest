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
    return await request_with_retry(client, "GET", url, **kwargs)


async def request_with_retry(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    """Any method, same retry rules as get_with_retry."""
    for attempt in range(RETRIES + 1):
        last = attempt == RETRIES
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            if last:
                raise
            log.info("%s %s failed (%s), retrying", method, url, type(exc).__name__)
            continue
        if response.status_code >= 500 and not last:
            log.info("%s %s returned %d, retrying", method, url, response.status_code)
            continue
        response.raise_for_status()
        return response
    raise AssertionError("unreachable")
