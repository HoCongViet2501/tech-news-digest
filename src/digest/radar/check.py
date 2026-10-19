"""Run every radar step and decide what to report today.

Order: vulnerabilities (by severity) -> new majors -> breaking releases, capped at
radar.max_entries. Only entries actually shown are marked as reported, so a
vulnerability cut by the cap is shown on a later day. Never raises.
"""

import logging
from collections.abc import Mapping
from datetime import date

import httpx
from pydantic import BaseModel, Field

from digest.config import Config
from digest.models import RadarEntry, RadarState
from digest.radar import github
from digest.radar.manifests import read_dependencies
from digest.radar.mapping import resolve_repos
from digest.radar.osv import check_vulnerabilities
from digest.radar.releases import check_releases

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "MEDIUM": 2, "LOW": 3}
KIND_ORDER = {"vulnerability": 0, "major": 1, "release": 2}

log = logging.getLogger(__name__)


class RadarResult(BaseModel):
    entries: list[RadarEntry] = Field(default_factory=list)
    state: RadarState  # to save once the digest was delivered
    repo_map: dict[str, str | None]
    warnings: list[str] = Field(default_factory=list)


def _order(entry: RadarEntry) -> tuple[int, int]:
    return KIND_ORDER[entry.kind], SEVERITY_ORDER.get((entry.severity or "").upper(), 4)


async def run_radar(
    cfg: Config,
    client: httpx.AsyncClient,
    state: RadarState,
    repo_map: dict[str, str | None],
    run_date: date,
    env: Mapping[str, str],
) -> RadarResult:
    unchanged = RadarResult(state=state, repo_map=repo_map)
    if not cfg.radar.enabled or not cfg.radar.manifests:
        return unchanged
    try:
        return await _run(cfg, client, state, repo_map, run_date, env)
    except Exception:
        log.exception("radar: failed unexpectedly, digest is sent without it")
        return unchanged.model_copy(update={"warnings": ["radar failed"]})


async def _run(
    cfg: Config,
    client: httpx.AsyncClient,
    state: RadarState,
    repo_map: dict[str, str | None],
    run_date: date,
    env: Mapping[str, str],
) -> RadarResult:
    token = github.resolve_token(cfg.radar.token_env, env)
    deps, warnings = await read_dependencies(cfg.radar, client, token)
    log.info("radar: %d direct dependencies", len(deps))

    reported = set(state.reported)
    try:
        vulns = await check_vulnerabilities(client, deps, reported)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("radar: OSV query failed (%s)", exc)
        warnings.append(f"osv: {type(exc).__name__}")
        vulns = []

    repo_map = await resolve_repos(client, deps, repo_map)
    releases, known = await check_releases(
        client, deps, repo_map, state.releases, token, cfg.radar.releases_per_repo
    )

    candidates = sorted((e for e in vulns + releases if e.key not in reported), key=_order)
    entries = candidates[: cfg.radar.max_entries]
    if len(candidates) > len(entries):
        log.info("radar: %d entries over the cap, left for later", len(candidates) - len(entries))

    marked = dict(state.reported)
    for entry in entries:
        for key in (entry.key, *entry.aliases):
            marked.setdefault(key, run_date.isoformat())
    return RadarResult(
        entries=entries,
        state=RadarState(reported=marked, releases=known),
        repo_map=repo_map,
        warnings=warnings,
    )
