"""Download one real response per source into tests/fixtures/.

Run manually when a source changes format:  uv run python scripts/refresh_fixtures.py
A failed download keeps the previous fixture and makes the script exit non-zero.
"""

import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
USER_AGENT = "tech-news-digest/0.1 (fixture refresh)"

# Radar fixtures mirror host/path, so the offline transport can serve them by URL.
RADAR = "radar"
# Ids the querybatch fixture returns for lodash@4.17.15; update both together.
OSV_LODASH_IDS = (
    "GHSA-29mw-wpgm-hmr9",
    "GHSA-35jh-r3h4-6jhm",
    "GHSA-f23m-r3pf-42rh",
    "GHSA-p6mc-m468-83gw",
    "GHSA-r5fr-rjxr-66jc",
    "GHSA-xxjr-mmjv-4gpg",
)

log = logging.getLogger("refresh_fixtures")


@dataclass(frozen=True)
class Target:
    name: str
    url: str
    filename: str
    json_body: dict | None = None  # set -> POST this body instead of GET


def build_targets(now: datetime) -> list[Target]:
    since = int((now - timedelta(hours=24)).timestamp())
    # Low points floor so the fixture also holds items below the default threshold.
    hn_query = urlencode(
        {"tags": "story", "numericFilters": f"created_at_i>{since},points>10", "hitsPerPage": 50}
    )
    week_ago = (now - timedelta(days=7)).date().isoformat()
    search_query = urlencode(
        {"q": f"created:>{week_ago} language:python", "sort": "stars", "order": "desc"}
    )
    return [
        Target(
            "hackernews",
            f"https://hn.algolia.com/api/v1/search_by_date?{hn_query}",
            "hackernews.json",
        ),
        Target(
            "github_trending",
            "https://github.com/trending/python?since=daily",
            "github_trending_python.html",
        ),
        Target(
            "github_search",
            f"https://api.github.com/search/repositories?{search_query}&per_page=30",
            "github_search.json",
        ),
        Target("lobsters", "https://lobste.rs/hottest.json", "lobsters.json"),
        Target("rss", "https://lwn.net/headlines/rss", "rss_lwn.xml"),
        # Summarizer context: article page, HN comments fallback, GitHub description fallback
        Target("article", "https://lwn.net/Articles/1096897/", "article_lwn.html"),
        Target("hn_item", "https://hn.algolia.com/api/v1/items/49868083", "hn_item.json"),
        Target(
            "github_repo", "https://api.github.com/repos/vectorize-io/hindsight", "github_repo.json"
        ),
        # Radar: package -> repo lookups for the hand-written manifests in radar/api.github.com
        Target(
            "npm_lodash",
            "https://registry.npmjs.org/lodash/latest",
            f"{RADAR}/registry.npmjs.org/lodash/latest.json",
        ),
        Target(
            "pypi_httpx",
            "https://pypi.org/pypi/httpx/json",
            f"{RADAR}/pypi.org/pypi/httpx/json.json",
        ),
        Target(
            "releases_lodash",
            "https://api.github.com/repos/lodash/lodash/releases?per_page=5",
            f"{RADAR}/api.github.com/repos/lodash/lodash/releases.json",
        ),
        Target(
            "releases_httpx",
            "https://api.github.com/repos/encode/httpx/releases?per_page=5",
            f"{RADAR}/api.github.com/repos/encode/httpx/releases.json",
        ),
        # Same queries, in the same order, as the offline radar run over the demo manifests.
        Target(
            "osv_querybatch",
            "https://api.osv.dev/v1/querybatch",
            f"{RADAR}/api.osv.dev/v1/querybatch.json",
            json_body={
                "queries": [
                    {"package": {"name": "lodash", "ecosystem": "npm"}, "version": "4.17.15"},
                    {"package": {"name": "httpx", "ecosystem": "PyPI"}, "version": "0.27.0"},
                ]
            },
        ),
        *(
            Target(
                f"osv_{vuln_id}",
                f"https://api.osv.dev/v1/vulns/{vuln_id}",
                f"{RADAR}/api.osv.dev/v1/vulns/{vuln_id}.json",
            )
            for vuln_id in OSV_LODASH_IDS
        ),
    ]


def refresh(client: httpx.Client, targets: list[Target], out_dir: Path) -> list[str]:
    """Fetch every target; return the names that failed."""
    out_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for target in targets:
        try:
            if target.json_body is None:
                response = client.get(target.url)
            else:
                response = client.post(target.url, json=target.json_body)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("%s: failed (%s), keeping previous fixture", target.name, exc)
            failures.append(target.name)
            continue
        path = out_dir / target.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        log.info("%s: wrote %s (%d bytes)", target.name, target.filename, len(response.content))
    return failures


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, timeout=15, follow_redirects=True) as client:
        failures = refresh(client, build_targets(datetime.now(UTC)), FIXTURES_DIR)
    if failures:
        log.error("failed: %s", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
