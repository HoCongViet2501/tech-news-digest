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
    ("lwn.net", "/headlines/rss", "rss_lwn.xml"),
    # Summarizer context, so offline runs with AI enabled get summaries.
    ("lwn.net", "/Articles/", "article_lwn.html"),
    ("hn.algolia.com", "/api/v1/items/", "hn_item.json"),
    ("api.github.com", "/repos/", "github_repo.json"),
    # Content store sources (W1). github_search shares the search route above.
    ("dev.to", "/api/articles", "devto_top.json"),
    ("huggingface.co", "/api/daily_papers", "hf_papers.json"),
    ("programming.dev", "/api/v3/post/list", "lemmy.json"),
    ("viblo.asia", "/api/posts/trending", "viblo_trending.json"),
    ("www.producthunt.com", "/feed", "producthunt.xml"),
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
