import asyncio
from datetime import UTC, datetime

import respx

from conftest import fixture_bytes
from digest.fetchers import viblo
from digest.http import make_client
from digest.models import Item

API = "https://viblo.asia/api/posts/trending"
NOW = datetime(2026, 11, 15, 1, 0, tzinfo=UTC)


def fetch() -> list[Item]:
    async def go() -> list[Item]:
        async with make_client() as client:
            return await viblo.fetch(client, now=NOW)

    return asyncio.run(go())


@respx.mock
def test_parses_fixture() -> None:
    respx.get(API).respond(200, content=fixture_bytes("viblo_trending.json"))

    [item] = fetch()

    assert item.source == "viblo"
    assert item.title.startswith("Agent Harness là gì?")
    assert item.url.startswith("https://viblo.asia/p/agent-harness-la-gi")
    assert item.score == 10  # points
    assert item.comments == 1
    assert item.tags == ["mayfest2026", "agent", "agent-harness"]
    assert item.published_at == datetime(2026, 5, 20, 0, 47, 43, tzinfo=UTC)
    assert item.description is not None
    assert item.description.startswith("Agent harness là toàn bộ phần mềm")
    assert len(item.description) <= 300


@respx.mock
def test_error_returns_empty() -> None:
    respx.get(API).respond(503)

    assert fetch() == []
