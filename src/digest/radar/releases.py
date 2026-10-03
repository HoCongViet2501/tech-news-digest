"""New releases of mapped dependencies: report new majors and releases mentioning "breaking".

Plain minor/patch releases are not reported. A release is reported once (its id goes
to data/radar_state.json) and only if published within `release_max_age_days`.
"""

import asyncio
import logging
from datetime import datetime, timedelta

import httpx

from digest.config import RadarConfig
from digest.http import get_with_retry
from digest.models import Dependency, RadarEntry
from digest.radar.github import API, github_headers
from digest.radar.mapping import RepoMap
from digest.radar.versions import numeric

RELEASES_API = API + "/repos/{repo}/releases"
PER_PAGE = 5
NOTES_MAX = 3000
CONCURRENCY = 8

log = logging.getLogger(__name__)


def release_id(repo: str, tag: str) -> str:
    return f"release:{repo}@{tag}"


async def fetch_releases(client: httpx.AsyncClient, repo: str, token: str | None) -> list[dict]:
    try:
        response = await get_with_retry(
            client,
            RELEASES_API.format(repo=repo),
            params={"per_page": PER_PAGE},
            headers=github_headers(token),
        )
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as exc:  # rate limit, renamed repo: skip this repo today
        log.warning("radar: cannot list releases of %s (%s)", repo, type(exc).__name__)
        return []


def _published(release: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(release["published_at"])
    except (KeyError, TypeError, ValueError):
        return None


def pick_releases(
    cfg: RadarConfig,
    dep: Dependency,
    repo: str,
    releases: list[dict],
    reported: dict[str, str],
    now: datetime,
) -> list[RadarEntry]:
    cutoff = now - timedelta(days=cfg.release_max_age_days)
    installed = numeric(dep.version)
    out = []
    for release in releases:
        tag = str(release.get("tag_name") or "")
        published = _published(release)
        if not tag or release.get("draft") or published is None or published < cutoff:
            continue
        if release.get("prerelease") and not cfg.include_prereleases:
            continue
        if release_id(repo, tag) in reported:
            continue
        version = numeric(tag)
        if installed and version and version <= installed:
            continue  # older line (e.g. a backport) or what we already run
        name = str(release.get("name") or "").strip()
        notes = str(release.get("body") or "")
        if installed and version and version[0] > installed[0]:
            kind = "major"
        elif "breaking" in f"{name}\n{notes}".lower():
            kind = "breaking"
        else:
            continue
        out.append(
            RadarEntry(
                id=release_id(repo, tag),
                kind=kind,
                ecosystem=dep.ecosystem,
                package=dep.name,
                installed=dep.version,
                version=tag,
                title=name or tag,
                url=str(release.get("html_url") or f"https://github.com/{repo}/releases"),
                published_at=published,
                notes=notes[:NOTES_MAX] or None,
            )
        )
    return out


async def check_releases(
    cfg: RadarConfig,
    client: httpx.AsyncClient,
    deps: list[Dependency],
    repo_map: RepoMap,
    reported: dict[str, str],
    now: datetime,
    token: str | None,
) -> list[RadarEntry]:
    """One releases request per mapped repo; several packages may share a repo."""
    repos = sorted({repo for dep in deps if (repo := repo_map.get(dep.key))})
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def one(repo: str) -> list[dict]:
        async with semaphore:
            return await fetch_releases(client, repo, token)

    fetched = dict(zip(repos, await asyncio.gather(*(one(r) for r in repos)), strict=True))
    entries: dict[str, RadarEntry] = {}
    for dep in deps:
        repo = repo_map.get(dep.key)
        if not repo:
            continue
        for entry in pick_releases(cfg, dep, repo, fetched[repo], reported, now):
            entries.setdefault(entry.id, entry)
    log.info("radar: %d repos checked, %d releases to report", len(repos), len(entries))
    return list(entries.values())
