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
