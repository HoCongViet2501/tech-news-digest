"""Shared async HTTP client: timeout, retries on 5xx, custom User-Agent."""

import logging
from typing import Any

import httpx
from pydantic import BaseModel

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
    return await _request(client, method, url, allow_not_modified=False, **kwargs)


async def _request(
    client: httpx.AsyncClient, method: str, url: str, *, allow_not_modified: bool, **kwargs: Any
) -> httpx.Response:
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
        if not (allow_not_modified and response.status_code == 304):
            response.raise_for_status()
        return response
    raise AssertionError("unreachable")


class ConditionalResponse(BaseModel):
    """Result of a conditional GET; on 304 the body is empty and the validators are kept."""

    status: int
    body: bytes = b""
    etag: str | None = None
    last_modified: str | None = None

    @property
    def not_modified(self) -> bool:
        return self.status == 304


async def conditional_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    **kwargs: Any,
) -> ConditionalResponse:
    """GET with If-None-Match / If-Modified-Since; same retry and error rules as get_with_retry.

    A 304 means "unchanged since the stored validators" and keeps the validators passed in
    unless the server sends new ones. A 200 returns exactly the validators the server sent.
    """
    headers = dict(kwargs.pop("headers", None) or {})
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    response = await _request(
        client, "GET", url, allow_not_modified=True, headers=headers, **kwargs
    )
    new_etag = response.headers.get("ETag")
    new_last_modified = response.headers.get("Last-Modified")
    if response.status_code == 304:
        return ConditionalResponse(
            status=304,
            etag=new_etag or etag,
            last_modified=new_last_modified or last_modified,
        )
    return ConditionalResponse(
        status=response.status_code,
        body=response.content,
        etag=new_etag,
        last_modified=new_last_modified,
    )
