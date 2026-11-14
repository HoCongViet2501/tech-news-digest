"""robots.txt parsing (RFC 9309 matching) and the per-run cache."""

import asyncio

import httpx
import pytest
import respx

from digest.enrich.robots import RobotsCache, RobotsRules
from digest.http import USER_AGENT, make_client

VIBLO = """User-agent: *
Disallow: /search
Disallow: /*.json
Disallow: /*.xml"""


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/p/django-auto-now-k74a96Ql4eO", True),
        ("/search?q=x", False),
        ("/api/posts.json", False),  # wildcard, unsupported by urllib.robotparser
        ("/sitemap.xml", False),
        ("/", True),
    ],
)
def test_wildcards(path: str, allowed: bool) -> None:
    assert RobotsRules.parse(VIBLO).allowed(f"https://viblo.asia{path}") is allowed


def test_longest_match_wins_and_allow_wins_ties() -> None:
    rules = RobotsRules.parse(
        "User-agent: *\nDisallow: /blog\nAllow: /blog/public\nDisallow: /x\nAllow: /x\n"
        "Disallow: /*.pdf$\n"
    )
    assert not rules.allowed("https://e.com/blog/secret")
    assert rules.allowed("https://e.com/blog/public/post")
    assert rules.allowed("https://e.com/x")
    assert not rules.allowed("https://e.com/a/file.pdf")
    assert rules.allowed("https://e.com/a/file.pdf?download=1")


def test_specific_group_overrides_star_and_groups_merge() -> None:
    text = """
# comment
User-agent: *
Disallow: /

User-agent: SomeBot
User-agent: tech-news-digest   # us
Disallow: /private
Sitemap: https://e.com/sitemap.xml
Allow: /private/ok
"""
    rules = RobotsRules.parse(text)
    assert rules.allowed("https://e.com/post")
    assert not rules.allowed("https://e.com/private/x")
    assert rules.allowed("https://e.com/private/ok")


def test_empty_disallow_allows_everything() -> None:
    assert RobotsRules.parse("User-agent: *\nDisallow:\n").allowed("https://e.com/any")


def check(cache: RobotsCache, url: str) -> str | None:
    async def go() -> str | None:
        async with make_client() as client:
            return await cache.check(client, url, timeout_s=5)

    return asyncio.run(go())


@respx.mock
def test_fetched_once_per_host_with_project_user_agent() -> None:
    route = respx.get("https://e.com/robots.txt").respond(200, text="User-agent: *\nDisallow: /x")
    cache = RobotsCache()

    async def go() -> list[str | None]:
        async with make_client() as client:
            return await asyncio.gather(
                *(cache.check(client, f"https://e.com/{p}", timeout_s=5) for p in "abx")
            )

    assert asyncio.run(go()) == [None, None, "robots: disallowed"]
    assert route.call_count == 1
    assert route.calls.last.request.headers["User-Agent"] == USER_AGENT


@respx.mock
def test_missing_robots_allows_everything() -> None:
    respx.get("https://e.com/robots.txt").respond(404)

    assert check(RobotsCache(), "https://e.com/anything") is None


@respx.mock
def test_server_error_skips_host() -> None:
    route = respx.get("https://e.com/robots.txt").respond(500)
    cache = RobotsCache()

    assert check(cache, "https://e.com/a") == "robots: http 500"
    assert check(cache, "https://e.com/b") == "robots: http 500"
    assert route.call_count == 3  # one call + 2 retries, then cached for the run


@respx.mock
def test_forbidden_robots_skips_host() -> None:
    respx.get("https://e.com/robots.txt").respond(403)

    assert check(RobotsCache(), "https://e.com/a") == "robots: http 403"


@respx.mock
def test_timeout_skips_host() -> None:
    respx.get("https://e.com/robots.txt").mock(side_effect=httpx.ConnectTimeout("slow"))

    assert check(RobotsCache(), "https://e.com/a") == "robots: ConnectTimeout"


def test_non_http_url_is_rejected_without_fetching() -> None:
    assert check(RobotsCache(), "ftp://e.com/file") == "invalid url"
