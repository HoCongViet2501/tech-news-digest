"""Vulnerabilities affecting the installed versions, via the free OSV API (no key).

One querybatch request covers every dependency with a known version; details are
then fetched only for vulnerabilities not reported before. Each vulnerability id
(or one of its aliases) is reported once per package.
"""

import asyncio
import logging

import httpx

from digest.models import Dependency, RadarEntry
from digest.radar.versions import numeric

QUERYBATCH = "https://api.osv.dev/v1/querybatch"
VULN_API = "https://api.osv.dev/v1/vulns/{id}"
VULN_PAGE = "https://osv.dev/vulnerability/{id}"
CONCURRENCY = 8

log = logging.getLogger(__name__)


def vuln_key(osv_id: str, dep: Dependency) -> str:
    return f"vuln:{osv_id}:{dep.ecosystem}:{dep.name}"


async def query_batch(client: httpx.AsyncClient, deps: list[Dependency]) -> list[list[str]] | None:
    """Vulnerability ids per dependency, in the same order; None if OSV failed."""
    queries = [
        {"package": {"name": d.name, "ecosystem": d.ecosystem}, "version": d.version} for d in deps
    ]
    try:
        response = await client.post(QUERYBATCH, json={"queries": queries})
        response.raise_for_status()
        results = response.json()["results"]
    except Exception as exc:
        log.warning("radar: OSV query failed (%s)", type(exc).__name__)
        return None
    if len(results) != len(queries):
        log.warning("radar: OSV returned %d results for %d queries", len(results), len(queries))
        return None
    return [[v["id"] for v in (r.get("vulns") or [])] for r in results]


async def vuln_details(client: httpx.AsyncClient, osv_id: str) -> dict | None:
    try:
        response = await client.get(VULN_API.format(id=osv_id))
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # still reported, just without a summary
        log.info("radar: no details for %s (%s)", osv_id, type(exc).__name__)
        return None


def _same_package(affected: dict, dep: Dependency) -> bool:
    pkg = affected.get("package") or {}
    return pkg.get("ecosystem") == dep.ecosystem and str(pkg.get("name", "")).lower() == (
        dep.name.lower()
    )


def fixed_version(details: dict, dep: Dependency) -> str | None:
    """Lowest fixed version above the installed one, from the ranges for this package."""
    installed = numeric(dep.version)
    fixes = [
        event["fixed"]
        for affected in details.get("affected") or []
        if _same_package(affected, dep)
        for rng in affected.get("ranges") or []
        for event in rng.get("events") or []
        if "fixed" in event
    ]
    ranked = [(v, f) for f in fixes if (v := numeric(f)) and (installed is None or v > installed)]
    return min(ranked)[1] if ranked else None


def _severity(details: dict) -> str | None:
    level = (details.get("database_specific") or {}).get("severity")
    return str(level).lower() if level else None


def _ghsa_first(ids: list[str]) -> list[str]:
    return sorted(ids, key=lambda i: (not i.startswith("GHSA-"), i))


async def check_vulnerabilities(
    client: httpx.AsyncClient, deps: list[Dependency], reported: dict[str, str]
) -> list[RadarEntry]:
    # Without a version OSV would list every vulnerability the package ever had.
    versioned = [d for d in deps if d.version]
    if not versioned:
        return []
    found = await query_batch(client, versioned)
    if found is None:
        return []

    pending = [
        (dep, osv_id)
        for dep, ids in zip(versioned, found, strict=True)
        for osv_id in _ghsa_first(ids)
        if vuln_key(osv_id, dep) not in reported
    ]
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def details(osv_id: str) -> dict | None:
        async with semaphore:
            return await vuln_details(client, osv_id)

    unique_ids = sorted({osv_id for _, osv_id in pending})
    fetched = dict(
        zip(unique_ids, await asyncio.gather(*(details(i) for i in unique_ids)), strict=True)
    )

    entries = []
    covered: set[str] = set()  # vuln keys (ids and aliases) already handled this run
    for dep, osv_id in pending:
        info = fetched[osv_id] or {}
        if info.get("withdrawn"):
            continue
        keys = {vuln_key(i, dep) for i in [osv_id, *(info.get("aliases") or [])]}
        if keys & covered or keys & reported.keys():
            continue
        covered |= keys
        entries.append(
            RadarEntry(
                id=vuln_key(osv_id, dep),
                kind="vulnerability",
                ecosystem=dep.ecosystem,
                package=dep.name,
                installed=dep.version,
                version=fixed_version(info, dep),
                title=str(info.get("summary") or osv_id),
                url=VULN_PAGE.format(id=osv_id),
                severity=_severity(info),
            )
        )
    log.info(
        "radar: %d dependencies checked on OSV, %d new vulnerabilities",
        len(versioned),
        len(entries),
    )
    return entries
