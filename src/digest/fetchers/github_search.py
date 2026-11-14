"""New GitHub repos gaining stars fast, via the Search API, with a spam filter.

The Search API only returns the owner's login, type and id, so the owner account age
cannot be checked without one extra request per repo; the filter relies on repo fields:
- no description: most star-farmed and malware-bait repos have none;
- forks > `max_fork_ratio` x stars: bought or bot forks (real new repos fork far less
  often than they are starred);
- stars >= `zero_fork_min_stars` and forks < `min_fork_ratio` x stars: lots of stars
  but nobody forks, the usual shape of bought stars;
- forks, archived or disabled repos.
"""

import logging
from datetime import datetime, timedelta

import httpx

from digest.fetchers.text import excerpt
from digest.http import get_with_retry
from digest.models import Item, item_id

API = "https://api.github.com/search/repositories"
SOURCE = "github_search"

log = logging.getLogger(__name__)


async def fetch(
    client: httpx.AsyncClient,
    *,
    now: datetime,
    token: str | None = None,
    days: int = 7,
    per_page: int = 30,
    max_fork_ratio: float = 0.5,
    min_fork_ratio: float = 0.005,
    zero_fork_min_stars: int = 200,
) -> list[Item]:
    """Repos created in the last `days`, most stars first. `token` is optional (rate limit)."""
    since = (now - timedelta(days=days)).date().isoformat()
    params: dict[str, str | int] = {
        "q": f"created:>{since}",
        "sort": "stars",
        "order": "desc",
        "per_page": per_page,
    }
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = await get_with_retry(client, API, params=params, headers=headers)
        repos = response.json()["items"]
    except Exception as exc:  # fetchers never raise; httpx messages hold no headers
        log.warning("github_search: fetch failed: %s", exc)
        return []
    items = []
    for repo in repos:
        reason = spam_reason(
            repo,
            max_fork_ratio=max_fork_ratio,
            min_fork_ratio=min_fork_ratio,
            zero_fork_min_stars=zero_fork_min_stars,
        )
        if reason:
            log.info("github_search: dropped %s (%s)", repo.get("full_name"), reason)
            continue
        items.append(_to_item(repo))
    log.info("github_search: kept %d of %d repos", len(items), len(repos))
    return items


def spam_reason(
    repo: dict, *, max_fork_ratio: float, min_fork_ratio: float, zero_fork_min_stars: int
) -> str | None:
    """Why this repo looks like spam, or None if it looks fine."""
    if repo.get("fork") or repo.get("archived") or repo.get("disabled"):
        return "fork, archived or disabled"
    if not (repo.get("description") or "").strip():
        return "no description"
    stars = repo.get("stargazers_count") or 0
    forks = repo.get("forks_count") or 0
    if forks > max_fork_ratio * stars:
        return f"forks/stars {forks}/{stars} above {max_fork_ratio}"
    if stars >= zero_fork_min_stars and forks < min_fork_ratio * stars:
        return f"forks/stars {forks}/{stars} below {min_fork_ratio}"
    return None


def _to_item(repo: dict) -> Item:
    url = repo["html_url"]
    return Item(
        id=item_id(url),
        source=SOURCE,
        title=repo["full_name"],
        url=url,
        score=repo.get("stargazers_count") or 0,
        published_at=datetime.fromisoformat(repo["created_at"]),
        tags=list(repo.get("topics") or []),
        description=excerpt(repo.get("description")),
    )
