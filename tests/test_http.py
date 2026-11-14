import asyncio

import httpx
import pytest
import respx

from digest.http import (
    USER_AGENT,
    ConditionalResponse,
    conditional_get,
    get_with_retry,
    make_client,
)

URL = "https://api.example/x"


def run_get(url: str = URL) -> httpx.Response:
    async def go() -> httpx.Response:
        async with make_client() as client:
            return await get_with_retry(client, url)

    return asyncio.run(go())


@respx.mock
def test_retries_5xx_then_succeeds() -> None:
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(502), httpx.Response(200, json={})]
    )

    assert run_get().status_code == 200
    assert route.call_count == 3


@respx.mock
def test_gives_up_after_two_retries() -> None:
    route = respx.get(URL).respond(500)

    with pytest.raises(httpx.HTTPStatusError):
        run_get()
    assert route.call_count == 3


@respx.mock
def test_4xx_is_not_retried() -> None:
    route = respx.get(URL).respond(404)

    with pytest.raises(httpx.HTTPStatusError):
        run_get()
    assert route.call_count == 1


@respx.mock
def test_transport_error_is_retried() -> None:
    route = respx.get(URL).mock(side_effect=[httpx.ConnectError("x"), httpx.Response(200)])

    assert run_get().status_code == 200
    assert route.call_count == 2


@respx.mock
def test_client_sends_custom_user_agent() -> None:
    route = respx.get(URL).respond(200)

    run_get()

    assert route.calls.last.request.headers["User-Agent"] == USER_AGENT
    assert "tech-news-digest" in USER_AGENT


def test_client_timeout_is_15s() -> None:
    assert make_client().timeout.read == 15


def run_conditional(**kwargs: str | None) -> ConditionalResponse:
    async def go() -> ConditionalResponse:
        async with make_client() as client:
            return await conditional_get(client, URL, **kwargs)

    return asyncio.run(go())


@respx.mock
def test_conditional_get_sends_validators_and_reports_304() -> None:
    route = respx.get(URL).respond(304)

    result = run_conditional(etag='"v1"', last_modified="Sat, 14 Nov 2026 10:00:00 GMT")

    sent = route.calls.last.request.headers
    assert sent["If-None-Match"] == '"v1"'
    assert sent["If-Modified-Since"] == "Sat, 14 Nov 2026 10:00:00 GMT"
    assert result.not_modified
    assert result.body == b""
    assert result.etag == '"v1"'
    assert result.last_modified == "Sat, 14 Nov 2026 10:00:00 GMT"
    assert route.call_count == 1


@respx.mock
def test_conditional_get_returns_body_and_new_validators() -> None:
    route = respx.get(URL).respond(
        200, content=b"feed", headers={"ETag": '"v2"', "Last-Modified": "Sun, 15 Nov 2026"}
    )

    result = run_conditional(etag='"v1"')

    assert "If-Modified-Since" not in route.calls.last.request.headers
    assert not result.not_modified
    assert result.status == 200
    assert result.body == b"feed"
    assert result.etag == '"v2"'
    assert result.last_modified == "Sun, 15 Nov 2026"


@respx.mock
def test_conditional_get_without_validators_sends_no_condition() -> None:
    route = respx.get(URL).respond(200, content=b"x")

    result = run_conditional()

    assert "If-None-Match" not in route.calls.last.request.headers
    assert result.etag is None and result.last_modified is None


@respx.mock
def test_conditional_get_keeps_retry_and_error_rules() -> None:
    respx.get(URL).mock(side_effect=[httpx.Response(503), httpx.Response(304)])
    assert run_conditional(etag='"v1"').not_modified

    respx.get(URL).respond(404)
    with pytest.raises(httpx.HTTPStatusError):
        run_conditional(etag='"v1"')
