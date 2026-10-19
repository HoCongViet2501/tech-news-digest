"""Known vulnerabilities for the declared versions, from OSV.dev (free, no key).

One querybatch request covers every dependency; details are then fetched only
for vulnerability ids not reported before. GHSA ids are preferred, and an
advisory already covered under one of its aliases (CVE, PYSEC, GO-...) is
dropped, so the same issue is never listed twice.
"""

import asyncio
import logging

import httpx

from digest.http import get_with_retry, request_with_retry
from digest.models import Dependency, RadarEntry
from digest.radar.versions import parse_version

QUERYBATCH = "https://api.osv.dev/v1/querybatch"
VULN = "https://api.osv.dev/v1/vulns/{id}"
WEB = "https://osv.dev/vulnerability/{id}"
BATCH_LIMIT = 1000  # OSV's max queries per querybatch request
CONCURRENCY = 8
TITLE_MAX = 160

log = logging.getLogger(__name__)


async def _query(client: httpx.AsyncClient, deps: list[Dependency]) -> list[list[str]]:
    """Vulnerability ids per dependency, in the same order."""
    ids: list[list[str]] = []
    for start in range(0, len(deps), BATCH_LIMIT):
        chunk = deps[start : start + BATCH_LIMIT]
        body = {
            "queries": [
                {"package": {"name": d.name, "ecosystem": d.ecosystem}, "version": d.version}
                for d in chunk
            ]
        }
        response = await request_with_retry(client, "POST", QUERYBATCH, json=body)
        results = response.json().get("results") or []
        if len(results) != len(chunk):
            raise ValueError(f"expected {len(chunk)} results, got {len(results)}")
        for result in results:
            if result.get("next_page_token"):
                log.info("radar: OSV returned more pages for one package; showing the first")
            ids.append([v["id"] for v in result.get("vulns") or []])
    return ids


async def _details(client: httpx.AsyncClient, vuln_id: str) -> dict:
    try:
        response = await get_with_retry(client, VULN.format(id=vuln_id))
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.info("radar: no OSV details for %s (%s)", vuln_id, type(exc).__name__)
        return {"id": vuln_id}


def _fixed_in(vuln: dict, dep: Dependency) -> str | None:
    """Lowest fixed version above the declared one, for this exact package."""
    installed = parse_version(dep.version)
    fixed = []
    for affected in vuln.get("affected") or []:
        package = affected.get("package") or {}
        if package.get("name") != dep.name or package.get("ecosystem") != dep.ecosystem:
            continue
        for rng in affected.get("ranges") or []:
            for event in rng.get("events") or []:
                version = parse_version(event.get("fixed"))
                if version and (installed is None or version > installed):
                    fixed.append((version, event["fixed"]))
    return min(fixed)[1] if fixed else None


def _title(vuln: dict) -> str:
    text = vuln.get("summary") or (vuln.get("details") or "").strip().split("\n")[0]
    text = text or vuln["id"]
    return text if len(text) <= TITLE_MAX else text[: TITLE_MAX - 1].rstrip() + "…"


def _entry(vuln: dict, dep: Dependency) -> RadarEntry:
    return RadarEntry(
        key=vuln["id"],
        kind="vulnerability",
        ecosystem=dep.ecosystem,
        package=dep.name,
        installed=dep.version,
        title=_title(vuln),
        url=WEB.format(id=vuln["id"]),
        projects=dep.projects,
        severity=(vuln.get("database_specific") or {}).get("severity"),
        fixed_in=_fixed_in(vuln, dep),
        aliases=list(vuln.get("aliases") or []),
    )


async def check_vulnerabilities(
    client: httpx.AsyncClient, deps: list[Dependency], already_reported: set[str]
) -> list[RadarEntry]:
    """Entries for vulnerabilities not in already_reported (by id or alias).

    Dependencies without a declared version are skipped: OSV would return every
    advisory ever published for them. Raises httpx.HTTPError / ValueError when the
    batch query itself fails; the caller turns that into a warning.
    """
    versioned = [d for d in deps if d.version]
    if not versioned:
        return []
    ids_per_dep = await _query(client, versioned)
    # GHSA first: it carries a severity, and its aliases cover the CVE/PYSEC/GO copies.
    candidates: dict[str, Dependency] = {}
    for dep, ids in zip(versioned, ids_per_dep, strict=True):
        for vuln_id in sorted(ids, key=lambda i: (not i.startswith("GHSA-"), i)):
            if vuln_id not in already_reported:
                candidates.setdefault(vuln_id, dep)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def one(vuln_id: str) -> dict:
        async with semaphore:
            return await _details(client, vuln_id)

    details = await asyncio.gather(*(one(i) for i in candidates))
    covered = set(already_reported)
    entries = []
    for (vuln_id, dep), vuln in zip(candidates.items(), details, strict=True):
        names = {vuln_id, *(vuln.get("aliases") or [])}
        if names & covered:
            continue
        covered |= names
        entries.append(_entry(vuln, dep))
    return entries
