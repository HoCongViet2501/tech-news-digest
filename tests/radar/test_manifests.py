import asyncio

import httpx
import respx

from conftest import fixture_bytes
from digest.config import RadarConfig
from digest.http import make_client
from digest.radar.manifests import (
    parse_go_mod,
    parse_manifest,
    parse_package_json,
    parse_pyproject,
    parse_requirements,
    read_dependencies,
)

CONTENTS = "https://api.github.com/repos/{repo}/contents/{path}"


def fixture_text(name: str) -> str:
    return fixture_bytes(name).decode("utf-8")


def test_package_json_keeps_lower_bound_and_skips_non_registry_specs() -> None:
    deps = parse_package_json(fixture_text("radar_manifest_package.json"), include_dev=False)

    assert deps == [
        ("npm", "lodash", "4.17.15"),
        ("npm", "express", "4.18.2"),
        ("npm", "left-pad", None),
        ("npm", "react", None),
    ]


def test_package_json_dev_dependencies_are_opt_in() -> None:
    deps = parse_package_json(fixture_text("radar_manifest_package.json"), include_dev=True)

    assert ("npm", "jest", "29.7.0") in deps


def test_requirements_txt() -> None:
    deps = parse_requirements(fixture_text("radar_manifest_requirements.txt"))

    assert deps == [
        ("PyPI", "django", "4.2.1"),
        ("PyPI", "requests", "2.31"),
        ("PyPI", "pydantic-core", "2.14.5"),
        ("PyPI", "uvicorn", None),
    ]


def test_pyproject_reads_project_dependencies() -> None:
    deps = parse_pyproject(fixture_text("radar_manifest_pyproject.toml"), include_dev=False)

    names = [name for _, name, _ in deps]
    assert "httpx" in names
    assert "pytest" not in names
    assert ("PyPI", "httpx", "0.28.1") in deps


def test_pyproject_dev_groups_are_opt_in() -> None:
    deps = parse_pyproject(fixture_text("radar_manifest_pyproject.toml"), include_dev=True)

    assert "pytest" in [name for _, name, _ in deps]


def test_go_mod_keeps_direct_requires_only() -> None:
    deps = parse_go_mod(fixture_text("radar_manifest_go.mod"))

    assert deps == [
        ("Go", "github.com/gin-gonic/gin", "1.9.1"),
        ("Go", "github.com/jackc/pgx/v5", "5.5.0"),
        ("Go", "github.com/docker/docker", "24.0.7"),
    ]


def test_unsupported_manifest_raises() -> None:
    try:
        parse_manifest("Cargo.toml", "", include_dev=False)
    except ValueError as exc:
        assert "Cargo.toml" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def read(cfg: RadarConfig, token: str | None = None):
    async def go():
        async with make_client() as client:
            return await read_dependencies(cfg, client, token)

    return asyncio.run(go())


@respx.mock
def test_reads_manifests_with_raw_accept_and_token() -> None:
    route = respx.get(CONTENTS.format(repo="me/web", path="package.json")).respond(
        200, content=fixture_bytes("radar_manifest_package.json")
    )
    cfg = RadarConfig(manifests=[{"repo": "me/web", "paths": ["package.json"]}])

    deps = read(cfg, token="secret-token")

    request = route.calls.last.request
    assert request.headers["Accept"] == "application/vnd.github.raw+json"
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert [d.name for d in deps] == ["lodash", "express", "left-pad", "react"]
    assert deps[0].manifests == ["me/web:package.json"]


@respx.mock
def test_broken_manifest_is_skipped_without_raising(caplog) -> None:
    respx.get(CONTENTS.format(repo="me/web", path="package.json")).respond(404)
    respx.get(CONTENTS.format(repo="me/web", path="go.mod")).respond(
        200, content=b"require (\n broken"
    )
    respx.get(CONTENTS.format(repo="me/web", path="requirements.txt")).respond(
        200, content=b"{ not json"
    )
    respx.get(CONTENTS.format(repo="me/api", path="pyproject.toml")).mock(
        side_effect=httpx.ConnectError("down")
    )
    respx.get(CONTENTS.format(repo="me/ok", path="go.mod")).respond(
        200, content=fixture_bytes("radar_manifest_go.mod")
    )
    respx.get(CONTENTS.format(repo="me/web", path="package-lock.json")).respond(200, content=b"{}")
    cfg = RadarConfig(
        manifests=[
            {"repo": "me/web", "paths": ["package.json", "go.mod", "package-lock.json"]},
            {"repo": "me/api", "paths": ["pyproject.toml"]},
            {"repo": "me/ok", "paths": ["go.mod"]},
        ]
    )

    deps = read(cfg, token="secret-token")

    assert [d.name for d in deps] == [
        "github.com/gin-gonic/gin",
        "github.com/jackc/pgx/v5",
        "github.com/docker/docker",
    ]
    assert "me/web:package.json (HTTP 404)" in caplog.text
    assert "unsupported manifest type: package-lock.json" in caplog.text
    assert "secret-token" not in caplog.text


@respx.mock
def test_same_package_in_two_repos_is_merged_with_lower_version() -> None:
    respx.get(CONTENTS.format(repo="me/a", path="requirements.txt")).respond(
        200, content=b"Django==5.0.1\n"
    )
    respx.get(CONTENTS.format(repo="me/b", path="requirements.txt")).respond(
        200, content=b"django==4.2.1\nflask==3.0.0\n"
    )
    cfg = RadarConfig(
        manifests=[
            {"repo": "me/a", "paths": ["requirements.txt"]},
            {"repo": "me/b", "paths": ["requirements.txt"]},
        ],
        ignore=["Flask"],
    )

    deps = read(cfg)

    assert len(deps) == 1
    assert deps[0].version == "4.2.1"
    assert deps[0].manifests == ["me/a:requirements.txt", "me/b:requirements.txt"]
