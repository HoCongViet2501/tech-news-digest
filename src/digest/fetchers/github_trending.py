"""GitHub Trending repos per language, scraped from HTML.

Trending has no official API. If a page yields no repos (error or markup
change), fall back to the Search API: repos created in the last 7 days,
sorted by stars.
"""

import logging
import re
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from digest.config import Config
from digest.http import get_with_retry
from digest.models import Item, item_id

TRENDING = "https://github.com/trending/{lang}?since=daily"
SEARCH = "https://api.github.com/search/repositories"
STARS_TODAY = re.compile(r"([\d,]+)\s+stars today")

log = logging.getLogger(__name__)


async def fetch(cfg: Config, client: httpx.AsyncClient, now: datetime) -> list[Item]:
    items: list[Item] = []
    for lang in cfg.sources.github_trending.languages:
        repos = await _trending(client, lang, now)
        if not repos:
            log.warning("github: trending/%s yielded 0 repos, falling back to search", lang)
            repos = await _search(client, lang, now)
        items.extend(repos)
    return items


async def _trending(client: httpx.AsyncClient, lang: str, now: datetime) -> list[Item]:
    try:
        response = await get_with_retry(client, TRENDING.format(lang=lang))
        return _parse_trending(response.text, lang, now)
    except Exception as exc:  # fetchers never raise
        log.warning("github: trending/%s failed: %s", lang, exc)
        return []


def _parse_trending(html: str, lang: str, now: datetime) -> list[Item]:
    items = []
    for row in BeautifulSoup(html, "html.parser").select("article.Box-row"):
        link = row.select_one("h2 a")
        if link is None or not link.get("href"):
            continue
        name = str(link["href"]).strip("/")
        url = f"https://github.com/{name}"
        desc_tag = row.select_one("p")
        desc = desc_tag.get_text(" ", strip=True) if desc_tag else ""
        match = STARS_TODAY.search(row.get_text(" ", strip=True))
        items.append(
            Item(
                id=item_id(url),
                source="github",
                title=f"{name}: {desc}" if desc else name,
                url=url,
                score=int(match.group(1).replace(",", "")) if match else 0,
                published_at=now,
                tags=[lang],
            )
        )
    return items


async def _search(client: httpx.AsyncClient, lang: str, now: datetime) -> list[Item]:
    week_ago = (now - timedelta(days=7)).date().isoformat()
    params = {
        "q": f"created:>{week_ago} language:{lang}",
        "sort": "stars",
        "order": "desc",
        "per_page": 30,
    }
    try:
        response = await get_with_retry(client, SEARCH, params=params)
        return [
            Item(
                id=item_id(repo["html_url"]),
                source="github",
                title=repo["full_name"],
                url=repo["html_url"],
                score=repo.get("stargazers_count") or 0,
                published_at=datetime.fromisoformat(repo["created_at"]),
                tags=[lang],
            )
            for repo in response.json()["items"]
        ]
    except Exception as exc:  # fetchers never raise
        log.warning("github: search fallback for %s failed: %s", lang, exc)
        return []
