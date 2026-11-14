import asyncio
from datetime import UTC, datetime

import respx

from conftest import fixture_bytes
from digest.fetchers import producthunt_rss
from digest.http import make_client
from digest.models import Item

FEED = "https://www.producthunt.com/feed"
NOW = datetime(2026, 11, 15, 1, 0, tzinfo=UTC)


def fetch() -> list[Item]:
    async def go() -> list[Item]:
        async with make_client() as client:
            return await producthunt_rss.fetch(client, now=NOW)

    return asyncio.run(go())


@respx.mock
def test_parses_fixture() -> None:
    respx.get(FEED).respond(200, content=fixture_bytes("producthunt.xml"))

    items = fetch()

    assert len(items) == 5
    first = items[0]
    assert first.source == "producthunt"
    assert first.title == "Drops"
    assert first.url == "https://www.producthunt.com/products/drops-4"
    assert first.score == 0
    assert first.published_at == datetime(2026, 11, 9, 22, 54, 34, tzinfo=UTC)
    assert first.description == "Discover new music through moments your friends love"


@respx.mock
def test_error_returns_empty() -> None:
    respx.get(FEED).respond(404)

    assert fetch() == []
