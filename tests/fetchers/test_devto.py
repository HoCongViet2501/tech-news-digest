import asyncio
from datetime import UTC, datetime

import httpx
import respx

from conftest import fixture_bytes
from digest.fetchers import devto
from digest.http import make_client
from digest.models import Item

API = "https://dev.to/api/articles"
NOW = datetime(2026, 11, 15, 1, 0, tzinfo=UTC)


def fetch(tags: list[str]) -> list[Item]:
    async def go() -> list[Item]:
        async with make_client() as client:
            return await devto.fetch(client, tags=tags, now=NOW, pause_seconds=0)

    return asyncio.run(go())


@respx.mock
def test_top_then_one_request_per_tag() -> None:
    top = respx.get(API, params={"top": "1"}).respond(200, content=fixture_bytes("devto_top.json"))
    java = respx.get(API, params={"tag": "java", "top": "7"}).respond(
        200, content=fixture_bytes("devto_tag_java.json")
    )

    items = fetch(["java"])

    assert top.called and java.called
    assert len(items) == 10
    first = items[0]
    assert first.source == "devto"
    assert first.title.startswith("Dev Opportunity Radar #25")
    assert first.score == 87  # public_reactions_count
    assert first.comments == 5
    assert first.tags == ["discuss", "community", "opportunities", "resources"]
    assert first.published_at == datetime(2026, 11, 13, 18, 28, 11, tzinfo=UTC)
    assert first.description is not None and first.description.startswith("TL;DR Welcome")
    assert first.discussion_url is None


@respx.mock
def test_cross_post_uses_canonical_url_and_keeps_devto_as_discussion() -> None:
    respx.get(API).respond(200, content=fixture_bytes("devto_tag_java.json"))

    crosspost = fetch([])[1]

    assert not crosspost.url.startswith("https://dev.to/")
    assert crosspost.discussion_url is not None
    assert crosspost.discussion_url.startswith("https://dev.to/")


@respx.mock
def test_article_listed_under_several_tags_is_returned_once() -> None:
    respx.get(API).respond(200, content=fixture_bytes("devto_tag_java.json"))

    items = fetch(["java", "node"])

    assert len(items) == 5
    assert len({i.id for i in items}) == 5


@respx.mock
def test_failing_tag_does_not_drop_the_others() -> None:
    respx.get(API, params={"tag": "node"}).respond(500)
    respx.get(API).respond(200, content=fixture_bytes("devto_top.json"))

    assert len(fetch(["node"])) == 5


@respx.mock
def test_network_error_returns_empty() -> None:
    respx.get(API).mock(side_effect=httpx.ConnectError("down"))

    assert fetch(["java"]) == []
