import asyncio
import json
from datetime import UTC, datetime

import respx

from conftest import NOW, fixture_bytes
from digest.config import RadarConfig
from digest.http import make_client
from digest.models import Dependency
from digest.radar.releases import check_releases, pick_releases

RELEASES = "https://api.github.com/repos/encode/httpx/releases"


def httpx_dep(version: str | None = "0.28.1") -> Dependency:
    return Dependency(ecosystem="PyPI", name="httpx", version=version)


def releases() -> list[dict]:
    return json.loads(fixture_bytes("radar_releases_encode_httpx.json"))


def pick(cfg: RadarConfig | None = None, dep: Dependency | None = None, reported=None, now=NOW):
    return pick_releases(
        cfg or RadarConfig(), dep or httpx_dep(), "encode/httpx", releases(), reported or {}, now
    )


def test_new_major_is_reported_and_patch_is_not() -> None:
    entries = pick()

    assert [(e.kind, e.version) for e in entries] == [("major", "1.0.0")]
    major = entries[0]
    assert major.id == "release:encode/httpx@1.0.0"
    assert major.installed == "0.28.1"
    assert major.url == "https://github.com/encode/httpx/releases/tag/1.0.0"
    assert major.title == "Version 1.0.0"
    assert major.published_at == datetime(2026, 9, 25, 10, 12, tzinfo=UTC)
    assert major.notes and "proxies" in major.notes


def test_notes_are_not_saved() -> None:
    assert "notes" not in pick()[0].model_dump()


def test_prerelease_with_breaking_is_opt_in() -> None:
    entries = pick(RadarConfig(include_prereleases=True))

    assert [(e.kind, e.version) for e in entries] == [
        ("major", "1.0.0"),
        ("breaking", "0.29.0b1"),
    ]


def test_already_reported_release_is_skipped() -> None:
    assert pick(reported={"release:encode/httpx@1.0.0": "2026-09-26"}) == []


def test_releases_older_than_max_age_are_skipped() -> None:
    later = datetime(2026, 10, 5, tzinfo=UTC)  # 1.0.0 is 10 days old by then

    assert pick(now=later) == []


def test_release_not_newer_than_installed_is_skipped() -> None:
    assert pick(dep=httpx_dep("1.0.0")) == []


def test_unknown_installed_version_reports_only_breaking() -> None:
    entries = pick(RadarConfig(include_prereleases=True), dep=httpx_dep(None))

    assert [e.version for e in entries] == ["0.29.0b1"]


def check(deps, repo_map, token=None):
    async def go():
        async with make_client() as client:
            return await check_releases(RadarConfig(), client, deps, repo_map, {}, NOW, token)

    return asyncio.run(go())


@respx.mock
def test_check_releases_queries_each_repo_once_with_token() -> None:
    route = respx.get(RELEASES).respond(
        200, content=fixture_bytes("radar_releases_encode_httpx.json")
    )
    deps = [httpx_dep(), Dependency(ecosystem="PyPI", name="httpx-extra", version="0.28.0")]
    repo_map = {"PyPI:httpx": "encode/httpx", "PyPI:httpx-extra": "encode/httpx"}

    entries = check(deps, repo_map, token="tok")

    assert route.call_count == 1
    assert route.calls.last.request.url.params["per_page"] == "5"
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok"
    assert [e.package for e in entries] == ["httpx"]


@respx.mock
def test_failed_repo_is_skipped(caplog) -> None:
    respx.get(RELEASES).respond(403)
    deps = [httpx_dep(), Dependency(ecosystem="PyPI", name="nogh", version="1.0")]

    entries = check(deps, {"PyPI:httpx": "encode/httpx", "PyPI:nogh": None})

    assert entries == []
    assert "cannot list releases of encode/httpx (HTTPStatusError)" in caplog.text
