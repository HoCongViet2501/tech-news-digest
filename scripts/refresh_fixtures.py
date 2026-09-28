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

log = logging.getLogger("refresh_fixtures")


@dataclass(frozen=True)
class Target:
    name: str
    url: str
    filename: str


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
    ]


def refresh(client: httpx.Client, targets: list[Target], out_dir: Path) -> list[str]:
    """Fetch every target; return the names that failed."""
    out_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for target in targets:
        try:
            response = client.get(target.url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("%s: failed (%s), keeping previous fixture", target.name, exc)
            failures.append(target.name)
            continue
        (out_dir / target.filename).write_bytes(response.content)
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
