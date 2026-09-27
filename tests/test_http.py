import asyncio

import httpx
import pytest
import respx

from digest.http import USER_AGENT, get_with_retry, make_client

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
