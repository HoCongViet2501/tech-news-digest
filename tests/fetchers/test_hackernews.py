import asyncio
from datetime import UTC, datetime

import respx
from conftest import NOW, fixture_bytes

from digest.config import Config
from digest.fetchers import hackernews
from digest.http import make_client

API = "https://hn.algolia.com/api/v1/search_by_date"


def fetch(cfg: Config):
    async def go():
        async with make_client() as client:
            return await hackernews.fetch(cfg, client, NOW)

    return asyncio.run(go())


@respx.mock
def test_parses_fixture(cfg: Config) -> None:
    respx.get(API).respond(200, content=fixture_bytes("hackernews.json"))

    items = fetch(cfg)

    assert len(items) == 47
    first = items[0]
    assert first.source == "hn"
    assert first.title == (
        "OpenAI halts training of latest models as reports mount of AI agents going rogue"
    )
    assert first.url.startswith("https://www.theguardian.com/technology/2026/sep/27/")
    assert first.discussion_url == "https://news.ycombinator.com/item?id=49868202"
    assert first.score == 42
    assert first.comments == 51
    assert first.published_at == datetime(2026, 9, 27, 16, 29, 38, tzinfo=UTC)


@respx.mock
def test_story_without_url_links_to_discussion(cfg: Config) -> None:
    respx.get(API).respond(200, content=fixture_bytes("hackernews.json"))

    items = fetch(cfg)

    ask = next(i for i in items if "49861047" in (i.discussion_url or ""))
    assert ask.url == "https://news.ycombinator.com/item?id=49861047"


@respx.mock
def test_queries_last_24h_with_low_points_floor(cfg: Config) -> None:
    route = respx.get(API).respond(200, json={"hits": []})

    fetch(cfg)

    params = route.calls.last.request.url.params
    assert params["tags"] == "story"
    # NOW - 24h = 2026-09-26T18:00Z; floor stays below min_points so include-keywords survive
    assert params["numericFilters"] == "created_at_i>1790445600,points>10"


@respx.mock
def test_error_returns_empty_list(cfg: Config) -> None:
    respx.get(API).respond(500)

    assert fetch(cfg) == []


@respx.mock
def test_malformed_payload_returns_empty_list(cfg: Config) -> None:
    respx.get(API).respond(200, content=b"<html>not json</html>")

    assert fetch(cfg) == []
