"""Map each dependency to the GitHub repo that publishes its releases.

npm: `repository.url` from the registry; PyPI: `project_urls`; Go: the module path
itself when it is on github.com. Results, including "no repo found" (None), are
cached in data/radar_map.json; lookups that failed for network reasons are not
cached, so they are retried on the next run. Without a repo a dependency is
still checked for vulnerabilities.
"""

import asyncio
import logging
import re
from urllib.parse import quote

import httpx

from digest.http import get_with_retry
from digest.models import Dependency

NPM = "https://registry.npmjs.org/{name}/latest"
PYPI = "https://pypi.org/pypi/{name}/json"
CONCURRENCY = 8

GITHUB_URL = re.compile(r"github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
# github.com/<first segment> paths that are not owners
NOT_OWNERS = {"sponsors", "orgs", "users", "apps", "marketplace", "features", "topics"}
PREFERRED_URL_KEYS = ("source", "repository", "code", "github")

log = logging.getLogger(__name__)


def github_repo(url: str | None) -> str | None:
    """'owner/name' from any github.com URL form (https, git+https, git@, ssh)."""
    match = GITHUB_URL.search(url or "")
    if not match or match.group(1).lower() in NOT_OWNERS:
        return None
    return f"{match.group(1)}/{match.group(2).removesuffix('.git')}"


def go_repo(module: str) -> str | None:
    parts = module.split("/")
    if len(parts) >= 3 and parts[0] == "github.com":
        return f"{parts[1]}/{parts[2]}"
    return None


def _npm_repo(data: dict) -> str | None:
    repository = data.get("repository")
    url = repository.get("url") if isinstance(repository, dict) else repository
    return github_repo(url) if isinstance(url, str) else None


def _pypi_repo(data: dict) -> str | None:
    info = data.get("info") or {}
    urls = info.get("project_urls") or {}
    ranked = sorted(
        urls.items(),
        key=lambda kv: next(
            (i for i, k in enumerate(PREFERRED_URL_KEYS) if k in kv[0].lower()),
            len(PREFERRED_URL_KEYS),
        ),
    )
    for _, url in ranked + [("home_page", info.get("home_page"))]:
        repo = github_repo(url)
        # Changelog/issue links still point at the right repo; sponsors pages do not.
        if repo:
            return repo
    return None


async def _lookup(client: httpx.AsyncClient, dep: Dependency) -> tuple[bool, str | None]:
    """(cacheable, repo). Network errors and 5xx are not cacheable."""
    if dep.ecosystem == "Go":
        return True, go_repo(dep.name)
    url = NPM.format(name=quote(dep.name, safe="@")) if dep.ecosystem == "npm" else None
    url = url or PYPI.format(name=dep.name)
    try:
        response = await get_with_retry(client, url)
        data = response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return True, None
        log.info("radar: repo lookup for %s failed (HTTP %d)", dep.key, exc.response.status_code)
        return False, None
    except (httpx.HTTPError, ValueError) as exc:
        log.info("radar: repo lookup for %s failed (%s)", dep.key, type(exc).__name__)
        return False, None
    return True, _npm_repo(data) if dep.ecosystem == "npm" else _pypi_repo(data)


async def resolve_repos(
    client: httpx.AsyncClient, deps: list[Dependency], cache: dict[str, str | None]
) -> dict[str, str | None]:
    """Updated copy of cache with an entry for every resolvable dependency key."""
    updated = dict(cache)
    missing = list({d.key: d for d in deps if d.key not in cache}.values())
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def one(dep: Dependency) -> tuple[bool, str | None]:
        async with semaphore:
            return await _lookup(client, dep)

    results = await asyncio.gather(*(one(d) for d in missing))
    for dep, (cacheable, repo) in zip(missing, results, strict=True):
        if cacheable:
            updated[dep.key] = repo
    return updated
