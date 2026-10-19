import asyncio

import httpx
import pytest
import respx

from conftest import fixture_bytes
from digest.http import make_client
from digest.models import Dependency
from digest.radar.mapping import _pypi_repo, github_repo, go_repo, resolve_repos
from digest.state import load_radar_map, save_radar_map

NPM_LODASH = "https://registry.npmjs.org/lodash/latest"
PYPI_HTTPX = "https://pypi.org/pypi/httpx/json"


def dep(ecosystem: str, name: str) -> Dependency:
    return Dependency(ecosystem=ecosystem, name=name, version="1.0.0")


def resolve(deps: list[Dependency], cache: dict[str, str | None] | None = None):
    async def go():
        async with make_client() as client:
            return await resolve_repos(client, deps, cache or {})

    return asyncio.run(go())


@pytest.mark.parametrize(
    ("url", "repo"),
    [
        ("git+https://github.com/lodash/lodash.git", "lodash/lodash"),
        ("https://github.com/encode/httpx", "encode/httpx"),
        ("git@github.com:owner/name.git", "owner/name"),
        ("https://github.com/encode/httpx/blob/master/CHANGELOG.md", "encode/httpx"),
        ("https://github.com/sponsors/samuelcolvin", None),
        ("https://gitlab.com/x/y", None),
        (None, None),
    ],
)
def test_github_repo(url: str | None, repo: str | None) -> None:
    assert github_repo(url) == repo


def test_go_module_paths() -> None:
    assert go_repo("github.com/jackc/pgx/v5") == "jackc/pgx"
    assert go_repo("github.com/spf13/cobra") == "spf13/cobra"
    assert go_repo("golang.org/x/text") is None


def test_pypi_prefers_source_over_sponsors_and_docs() -> None:
    data = {
        "info": {
            "project_urls": {
                "Funding": "https://github.com/sponsors/samuelcolvin",
                "Documentation": "https://docs.example.org",
                "Source": "https://github.com/pydantic/pydantic",
            }
        }
    }
    assert _pypi_repo(data) == "pydantic/pydantic"


@respx.mock
def test_resolves_from_real_registry_responses() -> None:
    respx.get(NPM_LODASH).respond(
        200, content=fixture_bytes("radar/registry.npmjs.org/lodash/latest.json")
    )
    respx.get(PYPI_HTTPX).respond(200, content=fixture_bytes("radar/pypi.org/pypi/httpx/json.json"))

    mapping = resolve(
        [dep("npm", "lodash"), dep("PyPI", "httpx"), dep("Go", "github.com/spf13/cobra")]
    )

    assert mapping == {
        "npm:lodash": "lodash/lodash",
        "PyPI:httpx": "encode/httpx",
        "Go:github.com/spf13/cobra": "spf13/cobra",
    }


@respx.mock
def test_scoped_npm_name_is_url_encoded() -> None:
    route = respx.get("https://registry.npmjs.org/@types%2Fnode/latest").respond(
        200, json={"repository": {"url": "https://github.com/DefinitelyTyped/DefinitelyTyped.git"}}
    )
    assert resolve([dep("npm", "@types/node")]) == {
        "npm:@types/node": "DefinitelyTyped/DefinitelyTyped"
    }
    assert route.called


@respx.mock
def test_cache_hits_skip_the_network_and_404_is_cached_as_none() -> None:
    respx.get("https://registry.npmjs.org/gone/latest").respond(404)

    mapping = resolve([dep("npm", "lodash"), dep("npm", "gone")], {"npm:lodash": "lodash/lodash"})

    assert mapping == {"npm:lodash": "lodash/lodash", "npm:gone": None}


@respx.mock
def test_network_failures_are_not_cached() -> None:
    respx.get("https://registry.npmjs.org/flaky/latest").respond(503)
    respx.get("https://registry.npmjs.org/down/latest").mock(side_effect=httpx.ConnectError("x"))

    assert resolve([dep("npm", "flaky"), dep("npm", "down")]) == {}


def test_radar_map_round_trip(tmp_path) -> None:
    path = tmp_path / "radar_map.json"
    assert load_radar_map(path) == {}
    save_radar_map(path, {"npm:lodash": "lodash/lodash", "npm:gone": None})
    assert load_radar_map(path) == {"npm:gone": None, "npm:lodash": "lodash/lodash"}
