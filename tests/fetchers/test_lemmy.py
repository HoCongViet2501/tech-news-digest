import asyncio
import json
from datetime import UTC, datetime

import respx

from conftest import fixture_bytes
from digest.fetchers import lemmy
from digest.http import make_client
from digest.models import Item

API = "https://programming.dev/api/v3/post/list"
NOW = datetime(2026, 11, 15, 1, 0, tzinfo=UTC)


def fetch() -> list[Item]:
    async def go() -> list[Item]:
        async with make_client() as client:
            return await lemmy.fetch(client, now=NOW)

    return asyncio.run(go())


@respx.mock
def test_parses_fixture() -> None:
    route = respx.get(API).respond(200, content=fixture_bytes("lemmy.json"))

    items = fetch()

    assert route.calls.last.request.url.params["sort"] == "TopDay"
    assert route.calls.last.request.url.params["limit"] == "50"
    assert len(items) == 10
    link = items[2]
    assert link.source == "lemmy"
    assert link.title.startswith("The Denny’s where Nvidia was born")
    assert link.url.startswith("https://www.nbcnews.com/")
    assert link.discussion_url == "https://programming.dev/post/59359510"
    assert link.score == 327
    assert link.comments == 39
    assert link.tags == ["programmer_humor"]
    assert link.published_at.tzinfo is not None


@respx.mock
def test_image_and_text_posts_link_to_the_thread() -> None:
    respx.get(API).respond(200, content=fixture_bytes("lemmy.json"))

    items = fetch()

    image, text = items[0], items[6]
    assert image.url == image.discussion_url == "https://programming.dev/post/59361999"
    assert text.url == text.discussion_url == "https://programming.dev/post/59389679"
    assert text.description is not None


@respx.mock
def test_nsfw_and_removed_posts_are_skipped() -> None:
    data = json.loads(fixture_bytes("lemmy.json"))
    data["posts"][0]["post"]["nsfw"] = True
    data["posts"][1]["post"]["removed"] = True
    respx.get(API).respond(200, json=data)

    assert len(fetch()) == 8


@respx.mock
def test_error_returns_empty() -> None:
    respx.get(API).respond(200, content=b"<html>maintenance</html>")

    assert fetch() == []
