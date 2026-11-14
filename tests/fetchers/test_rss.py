import asyncio
from datetime import UTC, datetime

import respx

from conftest import NOW, fixture_bytes
from digest.config import Config, RssFeedConfig
from digest.fetchers import rss
from digest.http import make_client

LWN = "https://lwn.net/headlines/rss"


def fetch(cfg: Config):
    async def go():
        async with make_client() as client:
            return await rss.fetch(cfg, client, NOW)

    return asyncio.run(go())


def with_feeds(cfg: Config, *feeds: RssFeedConfig) -> Config:
    cfg.sources.rss = list(feeds)
    return cfg


@respx.mock
def test_keeps_entries_from_last_36h(cfg: Config) -> None:
    respx.get(LWN).respond(200, content=fixture_bytes("rss_lwn.xml"))

    items = fetch(with_feeds(cfg, RssFeedConfig(name="lwn", url=LWN, base_score=70)))

    # 15 entries in the fixture; only one is newer than NOW - 36h
    assert len(items) == 1
    [item] = items
    assert item.source == "rss:lwn"
    assert item.title == "GDB 18.1 released"
    assert item.url == "https://lwn.net/Articles/1096897/"
    assert item.score == 70
    assert item.published_at == datetime(2026, 9, 26, 15, 3, 20, tzinfo=UTC)


@respx.mock
def test_broken_feed_does_not_affect_others(cfg: Config) -> None:
    respx.get(LWN).respond(200, content=fixture_bytes("rss_lwn.xml"))
    respx.get("https://broken.example/rss").respond(404)

    items = fetch(
        with_feeds(
            cfg,
            RssFeedConfig(name="broken", url="https://broken.example/rss"),
            RssFeedConfig(name="lwn", url=LWN),
        )
    )

    assert [i.source for i in items] == ["rss:lwn"]


def test_no_feeds_configured_returns_empty(cfg: Config) -> None:
    assert fetch(cfg) == []


def test_description_is_plain_text_excerpt(cfg: Config) -> None:
    with respx.mock:
        respx.get(LWN).respond(200, content=fixture_bytes("rss_lwn.xml"))
        [item] = fetch(with_feeds(cfg, RssFeedConfig(name="lwn", url=LWN)))

    assert item.description is not None
    assert "<" not in item.description
    assert len(item.description) <= 300


def fetch_feed(**kwargs: object) -> rss.FeedResult:
    async def go() -> rss.FeedResult:
        async with make_client() as client:
            return await rss.fetch_feed(
                client, name="lwn", url=LWN, base_weight=50, now=NOW, **kwargs
            )  # type: ignore[arg-type]

    return asyncio.run(go())


@respx.mock
def test_fetch_feed_returns_all_entries_and_validators() -> None:
    respx.get(LWN).respond(200, content=fixture_bytes("rss_lwn.xml"), headers={"ETag": '"abc"'})

    result = fetch_feed()

    assert not result.not_modified and result.error is None
    assert len(result.items) == 15  # no age limit by default
    assert {i.source for i in result.items} == {"rss:lwn"}
    assert {i.score for i in result.items} == {50}
    assert result.etag == '"abc"'
    assert result.last_modified is None


@respx.mock
def test_fetch_feed_max_age_matches_telegram_window() -> None:
    respx.get(LWN).respond(200, content=fixture_bytes("rss_lwn.xml"))

    assert len(fetch_feed(max_age=rss.MAX_AGE).items) == 1


@respx.mock
def test_fetch_feed_unchanged_feed_is_304() -> None:
    route = respx.get(LWN).respond(304)

    result = fetch_feed(etag='"abc"', last_modified="Sat, 14 Nov 2026 10:00:00 GMT")

    assert route.calls.last.request.headers["If-None-Match"] == '"abc"'
    assert result.not_modified
    assert result.items == []
    assert result.etag == '"abc"'
    assert result.last_modified == "Sat, 14 Nov 2026 10:00:00 GMT"


@respx.mock
def test_fetch_feed_error_keeps_old_validators() -> None:
    respx.get(LWN).respond(404)

    result = fetch_feed(etag='"abc"')

    assert result.error is not None
    assert result.items == []
    assert not result.not_modified
    assert result.etag == '"abc"'


@respx.mock
def test_fetch_feed_html_page_is_an_error() -> None:
    respx.get(LWN).respond(200, content=b"<html><body>Not a feed</body>")

    result = fetch_feed()

    assert result.error is not None
    assert result.items == []
