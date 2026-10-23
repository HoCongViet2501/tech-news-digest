"""Run the whole radar: manifests -> repo map -> OSV + releases -> ordered entries.

Never raises: any unexpected failure is logged and the digest goes out without a Radar.
"""

import asyncio
import logging
from datetime import datetime

import httpx
from pydantic import BaseModel

from digest.config import RadarConfig
from digest.models import RadarEntry
from digest.radar.manifests import read_dependencies
from digest.radar.mapping import RepoMap, map_repos
from digest.radar.osv import check_vulnerabilities
from digest.radar.releases import check_releases

KIND_ORDER = {"vulnerability": 0, "major": 1, "breaking": 2}
SEVERITY_ORDER = {"critical": 0, "high": 1, "moderate": 2, "medium": 2, "low": 3}

log = logging.getLogger(__name__)


class RadarResult(BaseModel):
    entries: list[RadarEntry]
    repo_map: RepoMap
    dependencies: int = 0


def _order(entry: RadarEntry) -> tuple[int, int, str]:
    return KIND_ORDER[entry.kind], SEVERITY_ORDER.get(entry.severity or "", 4), entry.package


async def run_radar(
    cfg: RadarConfig,
    client: httpx.AsyncClient,
    now: datetime,
    reported: dict[str, str],
    repo_map: RepoMap,
    token: str | None,
) -> RadarResult:
    """Vulnerabilities first (most severe first), then majors, then breaking releases."""
    try:
        deps = await read_dependencies(cfg, client, token)
        repo_map = await map_repos(client, deps, repo_map)
        vulns, releases = await asyncio.gather(
            check_vulnerabilities(client, deps, reported),
            check_releases(cfg, client, deps, repo_map, reported, now, token),
        )
    except Exception:
        log.exception("radar: failed unexpectedly, sending the digest without it")
        return RadarResult(entries=[], repo_map=repo_map)
    entries = sorted(vulns + releases, key=_order)
    if len(entries) > cfg.max_entries:
        log.info("radar: %d entries, keeping %d for later days", len(entries), cfg.max_entries)
    return RadarResult(
        entries=entries[: cfg.max_entries], repo_map=repo_map, dependencies=len(deps)
    )
