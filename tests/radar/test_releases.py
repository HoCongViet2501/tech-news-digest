import asyncio
import json

import pytest
import respx

from conftest import FIXTURES
from digest.http import make_client
from digest.models import Dependency, RadarState
from digest.radar.releases import check_releases, release_entries
from digest.radar.versions import major_line, parse_version
from digest.state import load_radar_state, save_radar_state

RELEASES = "https://api.github.com/repos/{repo}/releases"


def dep(name: str = "react", version: str | None = "18.2.0", projects=("me/web",)) -> Dependency:
    return Dependency(ecosystem="npm", name=name, version=version, projects=list(projects))


def rel(tag: str, body: str = "", **kw) -> dict:
    return {
        "tag_name": tag,
        "name": kw.pop("name", tag),
        "body": body,
        "html_url": f"https://github.com/o/r/releases/tag/{tag}",
        "draft": False,
        "prerelease": False,
    } | kw


@pytest.mark.parametrize(
    ("text", "version"),
    [
        ("v1.2.3", (1, 2, 3)),
        ("lodash@4.17.21", (4, 17, 21)),
        ("1.2", (1, 2, 0)),
        ("v5", (5, 0, 0)),
        ("release-candidate", None),
        (None, None),
    ],
)
def test_parse_version(text: str | None, version) -> None:
    assert parse_version(text) == version


def test_major_line_treats_each_zero_minor_as_its_own_line() -> None:
    assert major_line((2, 5, 1)) == (2,)
    assert major_line((0, 27, 0)) == (0, 27)


def test_first_sight_of_a_repo_reports_nothing() -> None:
    assert release_entries([dep()], "facebook/react", [rel("v19.0.0")], known_tags=None) == []


def test_only_first_release_of_a_new_major_line_is_flagged() -> None:
    releases = [rel("v19.0.1"), rel("v19.0.0", name="React 19"), rel("v18.3.0")]  # newest first

    entries = release_entries([dep()], "facebook/react", releases, known_tags=["v18.2.0"])

    assert [(e.kind, e.version, e.title, e.key) for e in entries] == [
        ("major", "v19.0.0", "React 19", "facebook/react@v19.0.0")
    ]
    assert entries[0].installed == "18.2.0"
    assert entries[0].projects == ["me/web"]


def test_new_major_already_known_is_not_flagged_again() -> None:
    entries = release_entries([dep()], "facebook/react", [rel("v19.1.0")], ["v19.0.0", "v18.2.0"])
    assert entries == []


def test_breaking_notes_flag_a_minor_release() -> None:
    releases = [rel("v18.4.0", body="### BREAKING changes\n- removed x"), rel("v18.3.0", "fixes")]

    entries = release_entries([dep()], "facebook/react", releases, known_tags=["v18.2.0"])

    assert [(e.kind, e.version, e.notes) for e in entries] == [
        ("release", "v18.4.0", "### BREAKING changes\n- removed x")
    ]


def test_zero_x_minor_bump_counts_as_major() -> None:
    entries = release_entries(
        [dep("httpx", "0.27.0")], "encode/httpx", [rel("0.28.0")], known_tags=["0.27.2"]
    )
    assert [e.kind for e in entries] == ["major"]


def test_drafts_prereleases_and_known_tags_are_skipped() -> None:
    releases = [
        rel("v20.0.0-rc.1", prerelease=True),
        rel("v20.0.0", draft=True),
        rel("v19.0.0"),
    ]
    assert release_entries([dep()], "facebook/react", releases, known_tags=["v19.0.0"]) == []


def test_unpinned_dependency_only_gets_breaking_releases() -> None:
    releases = [rel("v19.0.0"), rel("v18.9.0", body="Breaking: y")]
    entries = release_entries([dep(version=None)], "facebook/react", releases, ["v18.2.0"])
    assert [e.version for e in entries] == ["v18.9.0"]


def check(deps, repo_map, known, token="tok"):
    async def go():
        async with make_client() as client:
            return await check_releases(client, deps, repo_map, known, token, per_page=5)

    return asyncio.run(go())


@respx.mock
def test_check_releases_updates_known_tags_and_keeps_failed_repos() -> None:
    react = respx.get(RELEASES.format(repo="facebook/react")).respond(
        200, json=[rel("v19.0.0"), rel("v18.2.0")]
    )
    respx.get(RELEASES.format(repo="lodash/lodash")).respond(403)
    deps = [dep(), dep("react-dom"), dep("lodash", "4.17.15"), dep("left-pad")]
    repo_map = {
        "npm:react": "facebook/react",
        "npm:react-dom": "facebook/react",
        "npm:lodash": "lodash/lodash",
        "npm:left-pad": None,
    }
    known = {"facebook/react": ["v18.2.0"], "lodash/lodash": ["4.17.21"]}

    entries, updated = check(deps, repo_map, known)

    assert [e.key for e in entries] == ["facebook/react@v19.0.0"]
    assert updated == {"facebook/react": ["v19.0.0", "v18.2.0"], "lodash/lodash": ["4.17.21"]}
    assert react.call_count == 1  # react and react-dom share one request
    request = react.calls.last.request
    assert request.url.params["per_page"] == "5"
    assert request.headers["Authorization"] == "Bearer tok"


def test_radar_state_round_trip(tmp_path) -> None:
    path = tmp_path / "radar_state.json"
    assert load_radar_state(path) == RadarState()
    state = RadarState(reported={"GHSA-x": "2026-10-19"}, releases={"o/r": ["v1"]})
    save_radar_state(path, state)
    assert load_radar_state(path) == state


def test_real_releases_fixtures() -> None:
    def load(repo: str) -> list[dict]:
        path = FIXTURES / f"radar/api.github.com/repos/{repo}/releases.json"
        return json.loads(path.read_text(encoding="utf-8"))

    httpx_entries = release_entries(
        [dep("httpx", "0.27.0")], "encode/httpx", load("encode/httpx"), known_tags=["0.27.0"]
    )
    lodash_entries = release_entries(
        [dep("lodash", "4.17.15")], "lodash/lodash", load("lodash/lodash"), ["4.0.0", "3.0.0"]
    )

    assert [(e.kind, e.version) for e in httpx_entries] == [("major", "0.28.0")]
    assert httpx_entries[0].url == "https://github.com/encode/httpx/releases/tag/0.28.0"
    assert lodash_entries == []  # 4.18.x is the same major line and its notes are not breaking
