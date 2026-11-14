import asyncio
import json
import logging
from datetime import UTC, datetime

import pytest
import respx

from conftest import fixture_bytes
from digest.fetchers import github_search
from digest.http import make_client
from digest.models import Item

API = "https://api.github.com/search/repositories"
NOW = datetime(2026, 11, 15, 1, 0, tzinfo=UTC)


def fetch(**kwargs: object) -> list[Item]:
    async def go() -> list[Item]:
        async with make_client() as client:
            return await github_search.fetch(client, now=NOW, **kwargs)  # type: ignore[arg-type]

    return asyncio.run(go())


def repos() -> list[dict]:
    return json.loads(fixture_bytes("github_search_new.json"))["items"]


@respx.mock
def test_queries_repos_created_in_last_7_days_by_stars() -> None:
    route = respx.get(API).respond(200, content=fixture_bytes("github_search_new.json"))

    fetch()

    params = route.calls.last.request.url.params
    assert params["q"] == "created:>2026-11-08"
    assert params["sort"] == "stars"
    assert params["order"] == "desc"
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
def test_parses_fixture_and_drops_spam() -> None:
    respx.get(API).respond(200, content=fixture_bytes("github_search_new.json"))

    items = fetch()

    names = [i.title for i in items]
    assert len(repos()) == 30
    # fork farms (forks > half the stars) and stars nobody forks
    dropped = {r["full_name"] for r in repos()} - set(names)
    assert dropped == {
        "AIRzZ90/study-materials-fork",
        "duwqijlk/muse-protocol",
        "TomyJan/SnowLuma",
        "DevedevT/FFmpeg-GUI-Editor-Windows",
    }
    first = items[0]
    assert first.source == "github_search"
    assert first.title == "huamidev/GestaltX"
    assert first.url == "https://github.com/huamidev/GestaltX"
    assert first.score == 7536
    assert first.published_at == datetime(2026, 11, 9, 5, 15, 48, tzinfo=UTC)
    assert first.description is not None
    yogurt = next(i for i in items if i.title == "FrigadeHQ/yogurt")
    assert "electron" in yogurt.tags  # topics


def test_spam_rules() -> None:
    def reason(**repo: object) -> str | None:
        base = {"description": "A tool", "stargazers_count": 1000, "forks_count": 50}
        return github_search.spam_reason(
            base | repo, max_fork_ratio=0.5, min_fork_ratio=0.005, zero_fork_min_stars=200
        )

    assert reason() is None
    assert reason(description=None) == "no description"
    assert reason(description="  ") == "no description"
    assert reason(forks_count=600) is not None
    assert reason(forks_count=2) is not None
    assert reason(stargazers_count=150, forks_count=0) is None  # too few stars to judge
    assert reason(fork=True) is not None
    assert reason(archived=True) is not None


@respx.mock
def test_thresholds_are_parameters() -> None:
    respx.get(API).respond(200, content=fixture_bytes("github_search_new.json"))

    lenient = fetch(max_fork_ratio=10.0, min_fork_ratio=0.0)

    assert len(lenient) == 30


@respx.mock
def test_token_is_sent_but_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    route = respx.get(API).respond(403)
    caplog.set_level(logging.DEBUG)

    assert fetch(token="ghp_secret123") == []
    assert route.calls.last.request.headers["Authorization"] == "Bearer ghp_secret123"
    assert "ghp_secret123" not in caplog.text
