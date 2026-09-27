import asyncio

import respx
from conftest import NOW, fixture_bytes

from digest.config import Config
from digest.fetchers import github_trending
from digest.http import make_client

TRENDING_PY = "https://github.com/trending/python?since=daily"
SEARCH = "https://api.github.com/search/repositories"


def python_only(cfg: Config) -> Config:
    cfg.sources.github_trending.languages = ["python"]
    return cfg


def fetch(cfg: Config):
    async def go():
        async with make_client() as client:
            return await github_trending.fetch(cfg, client, NOW)

    return asyncio.run(go())


@respx.mock
def test_parses_trending_fixture(cfg: Config) -> None:
    respx.get(TRENDING_PY).respond(200, content=fixture_bytes("github_trending_python.html"))

    items = fetch(python_only(cfg))

    assert len(items) == 9
    first = items[0]
    assert first.source == "github"
    assert first.url == "https://github.com/vectorize-io/hindsight"
    assert first.title == "vectorize-io/hindsight: Hindsight: Agent Memory That Learns"
    assert first.score == 4463
    assert first.discussion_url is None
    assert first.tags == ["python"]
    assert first.published_at == NOW
    django = next(i for i in items if i.url == "https://github.com/django/django")
    assert django.score == 27


@respx.mock
def test_falls_back_to_search_when_trending_has_no_repos(cfg: Config) -> None:
    respx.get(TRENDING_PY).respond(200, content=b"<html><body>redesigned</body></html>")
    search = respx.get(SEARCH).respond(200, content=fixture_bytes("github_search.json"))

    items = fetch(python_only(cfg))

    assert len(items) == 30
    assert items[0].url == "https://github.com/Contrastive-LM/CLM"
    assert items[0].title == "Contrastive-LM/CLM"
    assert items[0].score == 1826
    query = search.calls.last.request.url.params["q"]
    # NOW - 7 days = 2026-09-20
    assert query == "created:>2026-09-20 language:python"


@respx.mock
def test_falls_back_to_search_when_trending_errors(cfg: Config) -> None:
    respx.get(TRENDING_PY).respond(500)
    respx.get(SEARCH).respond(200, content=fixture_bytes("github_search.json"))

    assert len(fetch(python_only(cfg))) == 30


@respx.mock
def test_one_language_failing_keeps_the_others(cfg: Config) -> None:
    cfg.sources.github_trending.languages = ["python", "go"]
    respx.get(TRENDING_PY).respond(200, content=fixture_bytes("github_trending_python.html"))
    respx.get("https://github.com/trending/go?since=daily").respond(500)
    respx.get(SEARCH).respond(403)

    assert len(fetch(cfg)) == 9
