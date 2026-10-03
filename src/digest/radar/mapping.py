"""Map packages to their GitHub repo so releases can be tracked.

npm: `repository` of the latest version; PyPI: `project_urls`; Go: the module path.
Answers (including "no repo") are cached in data/radar_map.json; lookups that
fail are not cached and are retried on the next run.
"""

import asyncio
import logging
import re
from urllib.parse import quote

import httpx

from digest.http import get_with_retry
from digest.models import Dependency

NPM_API = "https://registry.npmjs.org/{name}/latest"
PYPI_API = "https://pypi.org/pypi/{name}/json"
GITHUB_URL = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+)")
SHORTHAND = re.compile(r"^(?:github:)?([\w.-]+)/([\w.-]+)$")
PYPI_URL_KEYS = ("source", "source code", "repository", "code", "github", "homepage")
CONCURRENCY = 8

log = logging.getLogger(__name__)

RepoMap = dict[str, str | None]  # "ecosystem:name" -> "owner/repo", or None when there is none


def github_repo(url: str | None) -> str | None:
    if not url:
        return None
    match = GITHUB_URL.search(url) or SHORTHAND.match(url.strip())
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2).removesuffix('.git')}"


async def _npm(client: httpx.AsyncClient, name: str) -> str | None:
    response = await get_with_retry(client, NPM_API.format(name=quote(name, safe="@/")))
    repository = response.json().get("repository")
    if isinstance(repository, dict):
        repository = repository.get("url")
    return github_repo(repository if isinstance(repository, str) else None)


async def _pypi(client: httpx.AsyncClient, name: str) -> str | None:
    response = await get_with_retry(client, PYPI_API.format(name=quote(name)))
    info = response.json().get("info") or {}
    urls = {k.lower(): v for k, v in (info.get("project_urls") or {}).items()}
    candidates = [urls[k] for k in PYPI_URL_KEYS if k in urls] + list(urls.values())
    candidates.append(info.get("home_page"))
    return next((repo for url in candidates if (repo := github_repo(url))), None)


def _go(name: str) -> str | None:
    parts = name.split("/")
    return f"{parts[1]}/{parts[2]}" if parts[0] == "github.com" and len(parts) >= 3 else None


async def _lookup(client: httpx.AsyncClient, dep: Dependency) -> str | None:
    match dep.ecosystem:
        case "npm":
            return await _npm(client, dep.name)
        case "PyPI":
            return await _pypi(client, dep.name)
        case "Go":
            return _go(dep.name)


async def map_repos(client: httpx.AsyncClient, deps: list[Dependency], cache: RepoMap) -> RepoMap:
    """Return the cache extended with every dependency that could be resolved."""
    result = dict(cache)
    todo = [dep for dep in deps if dep.key not in cache]
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def resolve(dep: Dependency) -> None:
        async with semaphore:
            try:
                result[dep.key] = await _lookup(client, dep)
            except Exception as exc:  # registry down or unknown package: retry next run
                log.info("radar: no repo for %s yet (%s)", dep.key, type(exc).__name__)

    await asyncio.gather(*(resolve(dep) for dep in todo))
    found = sum(1 for dep in todo if result.get(dep.key))
    log.info("radar: mapped %d/%d new packages to GitHub repos", found, len(todo))
    return result
