import asyncio
from datetime import date

import httpx
import pytest

from digest.config import Config
from digest.http import make_client
from digest.models import Dependency, RadarEntry, RadarState
from digest.radar import check

DAY = date(2026, 10, 19)
DEP = Dependency(ecosystem="npm", name="lodash", version="4.17.15", projects=["me/web"])


def entry(key: str, kind: str = "vulnerability", severity: str | None = None, **kw) -> RadarEntry:
    return RadarEntry(
        key=key,
        kind=kind,
        ecosystem="npm",
        package="lodash",
        installed="4.17.15",
        title=key,
        url=f"https://example.com/{key}",
        severity=severity,
        **kw,
    )


def cfg(**radar) -> Config:
    radar = {"enabled": True, "manifests": [{"repo": "me/web", "paths": ["package.json"]}]} | radar
    return Config.model_validate({"profile": "x", "radar": radar})


@pytest.fixture
def steps(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace each radar step; tests set what they return."""
    found: dict = {"vulns": [], "releases": [], "known": {}, "warnings": [], "seen_token": None}

    async def read_dependencies(radar_cfg, client, token):
        found["seen_token"] = token
        return [DEP], list(found["warnings"])

    async def check_vulnerabilities(client, deps, reported):
        if isinstance(found["vulns"], Exception):
            raise found["vulns"]
        return [v for v in found["vulns"] if v.key not in reported]

    async def resolve_repos(client, deps, cache):
        return cache | {"npm:lodash": "lodash/lodash"}

    async def check_releases(client, deps, repo_map, known, token, per_page):
        return found["releases"], found["known"]

    for name, fn in [
        ("read_dependencies", read_dependencies),
        ("check_vulnerabilities", check_vulnerabilities),
        ("resolve_repos", resolve_repos),
        ("check_releases", check_releases),
    ]:
        monkeypatch.setattr(check, name, fn)
    return found


def run(config: Config, state: RadarState | None = None, env=None):
    async def go():
        async with make_client() as client:
            return await check.run_radar(config, client, state or RadarState(), {}, DAY, env or {})

    return asyncio.run(go())


def test_order_is_severity_then_majors_then_releases(steps: dict) -> None:
    steps["vulns"] = [
        entry("low", severity="LOW"),
        entry("crit", severity="CRITICAL"),
        entry("none"),
    ]
    steps["releases"] = [entry("o/r@v1.1", kind="release"), entry("o/r@v2", kind="major")]

    result = run(cfg())

    assert [e.key for e in result.entries] == ["crit", "low", "none", "o/r@v2", "o/r@v1.1"]


def test_cap_marks_only_shown_entries_so_the_rest_come_later(steps: dict) -> None:
    steps["vulns"] = [entry(f"v{i}", severity="HIGH", aliases=[f"CVE-{i}"]) for i in range(3)]
    steps["known"] = {"lodash/lodash": ["4.17.21"]}

    first = run(cfg(max_entries=2))
    second = run(cfg(max_entries=2), first.state)

    assert [e.key for e in first.entries] == ["v0", "v1"]
    assert first.state.reported == {k: "2026-10-19" for k in ["v0", "CVE-0", "v1", "CVE-1"]}
    assert first.state.releases == {"lodash/lodash": ["4.17.21"]}
    assert first.repo_map == {"npm:lodash": "lodash/lodash"}
    assert [e.key for e in second.entries] == ["v2"]


def test_osv_failure_becomes_a_warning(steps: dict) -> None:
    steps["vulns"] = httpx.ConnectError("down")
    steps["warnings"] = ["me/web:go.mod: HTTP 404"]
    steps["releases"] = [entry("o/r@v2", kind="major")]

    result = run(cfg())

    assert [e.key for e in result.entries] == ["o/r@v2"]
    assert result.warnings == ["me/web:go.mod: HTTP 404", "osv: ConnectError"]


def test_unexpected_error_never_raises(steps: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*args):
        raise RuntimeError("bug")

    monkeypatch.setattr(check, "resolve_repos", boom)
    state = RadarState(reported={"x": "2026-10-01"})

    result = run(cfg(), state)

    assert result.entries == [] and result.state == state and result.warnings == ["radar failed"]


def test_disabled_or_no_manifests_does_nothing(steps: dict) -> None:
    assert run(cfg(enabled=False)).entries == []
    assert run(cfg(manifests=[])).entries == []
    assert steps["seen_token"] is None


def test_first_configured_token_env_wins(steps: dict) -> None:
    run(cfg(), env={"GH_READ_TOKEN": "", "GITHUB_TOKEN": "job-token"})
    assert steps["seen_token"] == "job-token"
