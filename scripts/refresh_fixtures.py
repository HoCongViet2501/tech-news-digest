"""Download one real response per source into tests/fixtures/.

Run manually when a source changes format:  uv run python scripts/refresh_fixtures.py
A failed download keeps the previous fixture and makes the script exit non-zero.
"""

import json
import logging
import re
import sys
from collections.abc import Callable
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
    json_body: dict | None = None  # set -> POST this body instead of GET
    trim: Callable[[bytes], bytes] | None = None  # shrink / anonymize before writing


# People's names and profiles are not needed by any test; drop them from fixtures.
PERSONAL_KEYS = frozenset(
    {"user", "creator", "commentators", "authors", "submittedBy", "submittedOnDailyBy"}
)


def _scrub(value: object) -> object:
    if isinstance(value, dict):
        return {
            k: _scrub(v)
            for k, v in value.items()
            if k not in PERSONAL_KEYS
            # GitHub API link templates are bulk, not data
            and not (k.endswith("_url") and isinstance(v, str) and "api.github.com" in v)
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def trim_json(keep: int, key: str | None = None) -> Callable[[bytes], bytes]:
    """Keep the first `keep` entries of the top-level list (or of `data[key]`), scrubbed."""

    def trim(content: bytes) -> bytes:
        data = json.loads(content)
        if key is None:
            data = data[:keep]
        else:
            data[key] = data[key][:keep]
        return (json.dumps(_scrub(data), indent=1, ensure_ascii=False) + "\n").encode()

    return trim


def trim_atom(keep: int) -> Callable[[bytes], bytes]:
    """Keep the first `keep` Atom entries and drop their <author> blocks."""

    def trim(content: bytes) -> bytes:
        text = content.decode("utf-8")
        entries = list(re.finditer(r"\s*<entry>.*?</entry>", text, re.S))
        if len(entries) > keep:
            text = text[: entries[keep].start()] + text[entries[-1].end() :]
        return re.sub(r"\s*<author>.*?</author>", "", text, flags=re.S).encode("utf-8")

    return trim


def _scrub_json(content: bytes) -> bytes:
    return (json.dumps(_scrub(json.loads(content)), indent=1, ensure_ascii=False) + "\n").encode()


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
    new_repos_query = urlencode(
        {"q": f"created:>{week_ago}", "sort": "stars", "order": "desc", "per_page": 30}
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
        # Content store sources (W1). Trimmed to a few items, personal fields removed.
        Target(
            "devto_top",
            "https://dev.to/api/articles?top=1&per_page=30",
            "devto_top.json",
            trim=trim_json(5),
        ),
        Target(
            "devto_tag",
            "https://dev.to/api/articles?tag=java&top=7&per_page=30",
            "devto_tag_java.json",
            trim=trim_json(5),
        ),
        Target(
            "hf_papers",
            "https://huggingface.co/api/daily_papers",
            "hf_papers.json",
            trim=trim_json(5),
        ),
        # All 30 repos are kept: the spam-filter tests need the real bad ones.
        Target(
            "github_search_new",
            f"https://api.github.com/search/repositories?{new_repos_query}",
            "github_search_new.json",
            trim=_scrub_json,
        ),
        Target(
            "lemmy",
            "https://programming.dev/api/v3/post/list?sort=TopDay&limit=50",
            "lemmy.json",
            trim=trim_json(10, key="posts"),
        ),
        Target(
            "viblo",
            "https://viblo.asia/api/posts/trending",
            "viblo_trending.json",
            trim=trim_json(5, key="data"),
        ),
        Target(
            "producthunt",
            "https://www.producthunt.com/feed",
            "producthunt.xml",
            trim=trim_atom(5),
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
        content = target.trim(response.content) if target.trim else response.content
        path = out_dir / target.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        log.info("%s: wrote %s (%d bytes)", target.name, target.filename, len(content))
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
