import asyncio
import json
from pathlib import Path

import pytest
import respx

from conftest import fixture_bytes
from digest.http import make_client
from digest.models import Dependency
from digest.radar.mapping import github_repo, map_repos
from digest.state import load_radar_map, save_radar_map


def dep(ecosystem: str, name: str) -> Dependency:
    return Dependency(ecosystem=ecosystem, name=name, version="1.0.0")


def run(deps: list[Dependency], cache: dict | None = None):
    async def go():
        async with make_client() as client:
            return await map_repos(client, deps, cache or {})

    return asyncio.run(go())


@pytest.mark.parametrize(
    ("url", "repo"),
    [
        ("git+https://github.com/lodash/lodash.git", "lodash/lodash"),
        ("git@github.com:facebook/react.git", "facebook/react"),
        ("https://github.com/encode/httpx/blob/master/CHANGELOG.md", "encode/httpx"),
        ("github:stevemao/left-pad", "stevemao/left-pad"),
        ("expressjs/express", "expressjs/express"),
        ("https://gitlab.com/x/y", None),
        (None, None),
    ],
)
def test_github_repo(url: str | None, repo: str | None) -> None:
    assert github_repo(url) == repo


@respx.mock
def test_npm_uses_repository_of_latest_version() -> None:
    respx.get("https://registry.npmjs.org/lodash/latest").respond(
        200, content=fixture_bytes("radar_npm_lodash.json")
    )

    assert run([dep("npm", "lodash")]) == {"npm:lodash": "lodash/lodash"}


@respx.mock
def test_scoped_npm_package_keeps_scope_in_path() -> None:
    route = respx.get("https://registry.npmjs.org/@types/node/latest").respond(
        200, json={"repository": {"url": "https://github.com/DefinitelyTyped/DefinitelyTyped"}}
    )

    assert run([dep("npm", "@types/node")]) == {
        "npm:@types/node": "DefinitelyTyped/DefinitelyTyped"
    }
    assert route.called


@respx.mock
def test_pypi_prefers_source_url() -> None:
    respx.get("https://pypi.org/pypi/httpx/json").respond(
        200, content=fixture_bytes("radar_pypi_httpx.json")
    )

    assert run([dep("PyPI", "httpx")]) == {"PyPI:httpx": "encode/httpx"}


@respx.mock
def test_pypi_without_github_is_cached_as_none() -> None:
    respx.get("https://pypi.org/pypi/closed/json").respond(
        200, json={"info": {"project_urls": {"Homepage": "https://example.com"}}}
    )

    assert run([dep("PyPI", "closed")]) == {"PyPI:closed": None}


def test_go_module_path_maps_directly() -> None:
    deps = [dep("Go", "github.com/jackc/pgx/v5"), dep("Go", "golang.org/x/net")]

    assert run(deps) == {"Go:github.com/jackc/pgx/v5": "jackc/pgx", "Go:golang.org/x/net": None}


@respx.mock
def test_failed_lookup_is_not_cached_and_cached_keys_are_not_refetched() -> None:
    respx.get("https://registry.npmjs.org/flaky/latest").respond(503)
    cached = respx.get("https://registry.npmjs.org/lodash/latest")

    result = run([dep("npm", "flaky"), dep("npm", "lodash")], {"npm:lodash": "lodash/lodash"})

    assert result == {"npm:lodash": "lodash/lodash"}
    assert not cached.called


def test_radar_map_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "radar_map.json"
    save_radar_map(path, {"npm:lodash": "lodash/lodash", "Go:golang.org/x/net": None})

    assert load_radar_map(path) == {"Go:golang.org/x/net": None, "npm:lodash": "lodash/lodash"}
    assert json.loads(path.read_text(encoding="utf-8"))["Go:golang.org/x/net"] is None


def test_corrupt_radar_map_starts_over(tmp_path: Path) -> None:
    path = tmp_path / "radar_map.json"
    path.write_text("{oops", encoding="utf-8")

    assert load_radar_map(path) == {}
