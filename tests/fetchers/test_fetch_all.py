import asyncio

import httpx
import respx

from conftest import NOW, fixture_bytes
from digest.config import Config
from digest.fetchers import fetch_all
from digest.http import make_client
from digest.offline import offline_transport

HN = "https://hn.algolia.com/api/v1/search_by_date"
LOBSTERS = "https://lobste.rs/hottest.json"


def run(cfg: Config, only: list[str] | None, transport: httpx.AsyncBaseTransport | None = None):
    async def go():
        async with make_client(transport) as client:
            return await fetch_all(cfg, client, NOW, only)

    return asyncio.run(go())


@respx.mock
def test_one_failing_source_does_not_affect_others(cfg: Config) -> None:
    respx.get(HN).respond(200, content=fixture_bytes("hackernews.json"))
    respx.get(LOBSTERS).respond(500)

    results = run(cfg, ["hn", "lobsters"])

    assert len(results["hn"]) == 47
    assert results["lobsters"] == []


@respx.mock
def test_only_limits_the_sources(cfg: Config) -> None:
    respx.get(LOBSTERS).respond(200, content=fixture_bytes("lobsters.json"))

    results = run(cfg, ["lobsters"])

    assert list(results) == ["lobsters"]


@respx.mock
def test_disabled_source_is_skipped(cfg: Config) -> None:
    cfg.sources.hackernews.enabled = False
    respx.get(LOBSTERS).respond(200, content=fixture_bytes("lobsters.json"))

    results = run(cfg, ["hn", "lobsters"])

    assert "hn" not in results
    assert len(results["lobsters"]) == 25


def test_fetcher_that_raises_is_contained(cfg: Config, monkeypatch) -> None:
    from digest import fetchers

    async def boom(cfg, client, now):
        raise RuntimeError("bug in fetcher")

    monkeypatch.setitem(fetchers.FETCHERS, "hn", boom)
    results = run(cfg, ["hn", "lobsters"], offline_transport())

    assert results["hn"] == []
    assert len(results["lobsters"]) == 25


def test_offline_transport_serves_fixtures(cfg: Config) -> None:
    cfg.sources.github_trending.languages = ["python"]

    results = run(cfg, None, offline_transport())

    assert len(results["hn"]) == 47
    assert len(results["lobsters"]) == 25
    assert len(results["github"]) == 9


def test_offline_transport_404s_unknown_urls() -> None:
    async def go():
        async with make_client(offline_transport()) as client:
            return await client.get("https://unknown.example/x")

    assert asyncio.run(go()).status_code == 404
