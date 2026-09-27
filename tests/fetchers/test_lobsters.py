import asyncio
from datetime import datetime, timedelta, timezone

import respx
from conftest import NOW, fixture_bytes

from digest.config import Config
from digest.fetchers import lobsters
from digest.http import make_client

API = "https://lobste.rs/hottest.json"


def fetch(cfg: Config):
    async def go():
        async with make_client() as client:
            return await lobsters.fetch(cfg, client, NOW)

    return asyncio.run(go())


@respx.mock
def test_parses_fixture(cfg: Config) -> None:
    respx.get(API).respond(200, content=fixture_bytes("lobsters.json"))

    items = fetch(cfg)

    assert len(items) == 25
    first = items[0]
    assert first.source == "lobsters"
    assert first.title == "“They had no concept of a duty of care to their users.”"
    assert first.url == (
        "https://unsung.aresluna.org/they-had-no-concept-of-a-duty-of-care-to-their-users/"
    )
    assert first.discussion_url == (
        "https://lobste.rs/s/1colyy/they_had_no_concept_duty_care_their_users"
    )
    assert first.score == 55
    assert first.comments == 3
    assert first.tags == ["vim"]
    assert first.published_at == datetime(
        2026, 9, 27, 9, 44, 53, 153000, tzinfo=timezone(timedelta(hours=-5))
    )


@respx.mock
def test_text_post_uses_discussion_url(cfg: Config) -> None:
    story = {
        "short_id": "abc123",
        "title": "Ask: what are you working on?",
        "url": "",
        "score": 12,
        "comment_count": 4,
        "created_at": "2026-09-27T10:00:00.000-05:00",
        "comments_url": "https://lobste.rs/s/abc123/ask",
        "tags": ["ask"],
    }
    respx.get(API).respond(200, json=[story])

    [item] = fetch(cfg)

    assert item.url == "https://lobste.rs/s/abc123/ask"


@respx.mock
def test_error_returns_empty_list(cfg: Config) -> None:
    respx.get(API).respond(503)

    assert fetch(cfg) == []
