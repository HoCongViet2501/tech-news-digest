"""DIGEST_OFFLINE=1: serve tests/fixtures/ instead of the network."""

import os
from pathlib import Path

import httpx

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"

# (host, path prefix) -> fixture file
ROUTES: list[tuple[str, str, str]] = [
    ("hn.algolia.com", "/api/v1/search_by_date", "hackernews.json"),
    ("github.com", "/trending/", "github_trending_python.html"),
    ("api.github.com", "/search/repositories", "github_search.json"),
    ("lobste.rs", "/hottest.json", "lobsters.json"),
    ("www.reddit.com", "/r/", "reddit_programming.json"),
    ("lwn.net", "/headlines/rss", "rss_lwn.xml"),
]


def is_offline() -> bool:
    return os.environ.get("DIGEST_OFFLINE") == "1"


def _handler(request: httpx.Request) -> httpx.Response:
    for host, prefix, filename in ROUTES:
        path = FIXTURES_DIR / filename
        if request.url.host == host and request.url.path.startswith(prefix) and path.is_file():
            return httpx.Response(200, content=path.read_bytes())
    return httpx.Response(404)


def offline_transport() -> httpx.MockTransport:
    return httpx.MockTransport(_handler)
