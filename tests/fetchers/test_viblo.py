import asyncio
import json
from datetime import UTC, datetime

import respx

from conftest import fixture_bytes
from digest.fetchers import viblo
from digest.http import make_client
from digest.models import Item

EDITORS = "https://viblo.asia/api/posts/editors-choice"
NEWEST = "https://viblo.asia/api/posts/newest"
NOW = datetime(2026, 11, 15, 1, 0, tzinfo=UTC)


def fetch() -> list[Item]:
    async def go() -> list[Item]:
        async with make_client() as client:
            return await viblo.fetch(client, now=NOW)

    return asyncio.run(go())


@respx.mock
def test_editors_choice_then_newest_20_each() -> None:
    editors = respx.get(EDITORS).respond(200, content=fixture_bytes("viblo_editors_choice.json"))
    newest = respx.get(NEWEST).respond(200, content=fixture_bytes("viblo_newest.json"))

    items = fetch()

    assert editors.calls.last.request.url.params["limit"] == "20"
    assert newest.calls.last.request.url.params["limit"] == "20"
    assert len(items) == 6
    first = items[0]
    assert first.source == "viblo"
    assert first.title.startswith("Tôi đã đọc 5.000+ paper về Cybersecurity")
    assert first.url.startswith("https://viblo.asia/p/toi-da-doc-5000-paper")
    assert first.score == 4339  # views_count
    assert first.comments == 5  # comments_count
    assert first.tags[:2] == ["cyber-security", "artificial-intelligent"]
    assert first.published_at == datetime(2026, 1, 16, 20, 10, 40, tzinfo=UTC)
    assert first.description is not None and len(first.description) <= 300
    assert items[3].title.startswith("Django: dùng auto_now")  # newest follow


@respx.mock
def test_post_in_both_feeds_is_returned_once() -> None:
    picked = json.loads(fixture_bytes("viblo_editors_choice.json"))
    newest = json.loads(fixture_bytes("viblo_newest.json"))
    newest["data"].insert(0, picked["data"][0])
    respx.get(EDITORS).respond(200, json=picked)
    respx.get(NEWEST).respond(200, json=newest)

    items = fetch()

    assert len(items) == 6
    assert len({i.id for i in items}) == 6


@respx.mock
def test_one_feed_failing_keeps_the_other() -> None:
    respx.get(EDITORS).respond(503)
    respx.get(NEWEST).respond(200, content=fixture_bytes("viblo_newest.json"))

    assert len(fetch()) == 3


@respx.mock
def test_error_returns_empty() -> None:
    respx.get(EDITORS).respond(503)
    respx.get(NEWEST).respond(404)

    assert fetch() == []
