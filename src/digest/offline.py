"""DIGEST_OFFLINE=1: serve tests/fixtures/ instead of the network."""

import json
import os
import re
from pathlib import Path

import httpx

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
OFFLINE_CHAT_ID = "42"  # chat id used by the Telegram fixtures

# (host, path prefix) -> fixture file
ROUTES: list[tuple[str, str, str]] = [
    ("hn.algolia.com", "/api/v1/search_by_date", "hackernews.json"),
    ("github.com", "/trending/", "github_trending_python.html"),
    ("api.github.com", "/search/repositories", "github_search.json"),
    ("lobste.rs", "/hottest.json", "lobsters.json"),
    ("lwn.net", "/headlines/rss", "rss_lwn.xml"),
]

# Radar: (host, path regex) -> fixture name filled from the match groups; 404 if absent.
PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "api.github.com",
        re.compile(r"^/repos/[^/]+/[^/]+/contents/(?:.*/)?([^/]+)$"),
        "radar_manifest_{0}",
    ),
    (
        "api.github.com",
        re.compile(r"^/repos/([^/]+)/([^/]+)/releases$"),
        "radar_releases_{0}_{1}.json",
    ),
    ("registry.npmjs.org", re.compile(r"^/([^/@]+)/latest$"), "radar_npm_{0}.json"),
    ("pypi.org", re.compile(r"^/pypi/([^/]+)/json$"), "radar_pypi_{0}.json"),
    ("api.osv.dev", re.compile(r"^/v1/vulns/([\w-]+)$"), "radar_osv_{0}.json"),
    ("api.telegram.org", re.compile(r"^/bot[^/]+/(getUpdates)$"), "telegram_{0}.json"),
]


def is_offline() -> bool:
    return os.environ.get("DIGEST_OFFLINE") == "1"


def _osv_querybatch(request: httpx.Request) -> httpx.Response:
    """Answer each query from radar_osv_querybatch_<name>.json, or with no vulnerabilities."""
    results = []
    for query in json.loads(request.content)["queries"]:
        path = FIXTURES_DIR / f"radar_osv_querybatch_{query['package']['name']}.json"
        results.append(json.loads(path.read_text())["results"][0] if path.is_file() else {})
    return httpx.Response(200, json={"results": results})


def _handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host == "api.osv.dev" and path == "/v1/querybatch":
        return _osv_querybatch(request)
    for route_host, prefix, filename in ROUTES:
        fixture = FIXTURES_DIR / filename
        if host == route_host and path.startswith(prefix) and fixture.is_file():
            return httpx.Response(200, content=fixture.read_bytes())
    for route_host, pattern, template in PATTERNS:
        match = pattern.match(path) if host == route_host else None
        fixture = FIXTURES_DIR / template.format(*match.groups()) if match else None
        if fixture is not None and fixture.is_file():
            return httpx.Response(200, content=fixture.read_bytes())
    return httpx.Response(404)


def offline_transport() -> httpx.MockTransport:
    return httpx.MockTransport(_handler)
