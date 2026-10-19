"""New releases of the repos behind each dependency.

Only two kinds are reported: the first release of a new major line ahead of the
declared version, and releases whose notes mention "breaking". The first time a
repo is seen its current releases become the baseline and nothing is reported,
so day one does not flood the digest with old releases.
"""

import asyncio
import logging

import httpx

from digest.http import get_with_retry
from digest.models import Dependency, RadarEntry
from digest.radar import github
from digest.radar.versions import major_line, parse_version

RELEASES = github.API + "/repos/{repo}/releases"
CONCURRENCY = 8
KNOWN_TAGS_KEPT = 50

log = logging.getLogger(__name__)


async def fetch_releases(
    client: httpx.AsyncClient, repo: str, token: str | None, per_page: int
) -> list[dict] | None:
    """Newest first; None when the request failed."""
    try:
        response = await get_with_retry(
            client,
            RELEASES.format(repo=repo),
            params={"per_page": per_page},
            headers=github.headers(token),
        )
        data = response.json()
    except httpx.HTTPStatusError as exc:
        log.warning("radar: releases for %s failed (HTTP %d)", repo, exc.response.status_code)
        return None
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("radar: releases for %s failed (%s)", repo, type(exc).__name__)
        return None
    return data if isinstance(data, list) else None


def release_entries(
    deps: list[Dependency], repo: str, releases: list[dict], known_tags: list[str] | None
) -> list[RadarEntry]:
    if known_tags is None:
        return []
    # The oldest declared version among the dependencies published from this repo.
    dep = min(deps, key=lambda d: parse_version(d.version) or (10**9, 0, 0))
    installed = parse_version(dep.version)
    lines = {major_line(v) for v in map(parse_version, known_tags) if v}
    projects = sorted({p for d in deps for p in d.projects})
    entries = []
    for release in reversed(releases):  # oldest first, so a new line is flagged once
        tag = release.get("tag_name") or ""
        if not tag or tag in known_tags or release.get("draft") or release.get("prerelease"):
            continue
        version = parse_version(tag)
        notes = release.get("body") or ""
        line = major_line(version) if version else None
        if line and installed and line > major_line(installed) and line not in lines:
            kind = "major"
        elif "breaking" in notes.lower():
            kind = "release"
        else:
            continue
        if line:
            lines.add(line)
        entries.append(
            RadarEntry(
                key=f"{repo}@{tag}",
                kind=kind,
                ecosystem=dep.ecosystem,
                package=dep.name,
                installed=dep.version,
                title=release.get("name") or tag,
                url=release.get("html_url") or f"https://github.com/{repo}/releases/tag/{tag}",
                projects=projects,
                version=tag,
                notes=notes or None,
            )
        )
    return entries


async def check_releases(
    client: httpx.AsyncClient,
    deps: list[Dependency],
    repo_map: dict[str, str | None],
    known: dict[str, list[str]],
    token: str | None,
    per_page: int,
) -> tuple[list[RadarEntry], dict[str, list[str]]]:
    """(entries, updated known tags per repo). Repos whose request failed keep their tags."""
    by_repo: dict[str, list[Dependency]] = {}
    for dep in deps:
        repo = repo_map.get(dep.key)
        if repo:
            by_repo.setdefault(repo, []).append(dep)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def one(repo: str) -> list[dict] | None:
        async with semaphore:
            return await fetch_releases(client, repo, token, per_page)

    repos = list(by_repo)
    results = await asyncio.gather(*(one(r) for r in repos))
    entries: list[RadarEntry] = []
    updated = dict(known)
    for repo, releases in zip(repos, results, strict=True):
        if releases is None:
            continue
        entries.extend(release_entries(by_repo[repo], repo, releases, known.get(repo)))
        tags = [r["tag_name"] for r in releases if r.get("tag_name")]
        previous = known.get(repo, [])
        updated[repo] = (tags + [t for t in previous if t not in tags])[:KNOWN_TAGS_KEPT]
    return entries, updated
