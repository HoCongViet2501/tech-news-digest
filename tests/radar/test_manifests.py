import asyncio

import pytest
import respx

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


def test_package_json_reduces_ranges_to_lower_bound() -> None:
    text = """{
      "dependencies": {
        "lodash": "^4.17.15", "react": "~18.2", "vue": "3.x", "next": ">=14.1.0 <15",
        "@scope/pkg": "2.0.0-beta.1", "any": "*", "local": "file:../lib",
        "fork": "github:me/fork", "alias": "npm:other@1.0.0", "either": "1.2.3 || 2.0.0"
      },
      "devDependencies": {"eslint": "^9.0.0"}
    }"""
    assert dict(parse_package_json(text, include_dev=False)) == {
        "lodash": "4.17.15",
        "react": "18.2.0",
        "vue": "3.0.0",
        "next": "14.1.0",
        "@scope/pkg": "2.0.0-beta.1",
        "any": None,
        "local": None,
        "fork": None,
        "alias": None,
        "either": "1.2.3",
    }


def test_package_json_dev_dependencies_only_when_enabled() -> None:
    text = '{"dependencies": {"a": "1.0.0"}, "devDependencies": {"b": "2.0.0"}}'
    assert parse_package_json(text, include_dev=False) == [("a", "1.0.0")]
    assert parse_package_json(text, include_dev=True) == [("a", "1.0.0"), ("b", "2.0.0")]


def test_requirements_txt() -> None:
    text = """
# comment
Django==5.1.2
requests>=2.31,<3
pydantic_core~=2.20.0  # trailing comment
Flask[async]==3.0.0 ; python_version >= "3.12"
uvicorn
numpy==2.*
-r base.txt
-e git+https://github.com/x/y.git#egg=y
"""
    assert parse_requirements(text) == [
        ("django", "5.1.2"),
        ("requests", "2.31"),
        ("pydantic-core", "2.20.0"),
        ("flask", "3.0.0"),
        ("uvicorn", None),
        ("numpy", None),
    ]


def test_pyproject_project_and_dev_groups() -> None:
    text = """
[project]
name = "x"
dependencies = ["httpx>=0.28.1", "PyYAML==6.0.3"]

[project.optional-dependencies]
docs = ["mkdocs>=1.6"]

[dependency-groups]
dev = ["pytest>=9.1.1", {include-group = "docs"}]
"""
    assert parse_pyproject(text, include_dev=False) == [("httpx", "0.28.1"), ("pyyaml", "6.0.3")]
    assert parse_pyproject(text, include_dev=True) == [
        ("httpx", "0.28.1"),
        ("pyyaml", "6.0.3"),
        ("mkdocs", "1.6"),
        ("pytest", "9.1.1"),
    ]


def test_go_mod_skips_indirect() -> None:
    text = """module example.com/api

go 1.23

require github.com/spf13/cobra v1.8.0

require (
\tgithub.com/gin-gonic/gin v1.6.0
\tgithub.com/jackc/pgx/v5 v5.7.1
\tgolang.org/x/text v0.14.0 // indirect
)
"""
    assert parse_go_mod(text) == [
        ("github.com/spf13/cobra", "v1.8.0"),
        ("github.com/gin-gonic/gin", "v1.6.0"),
        ("github.com/jackc/pgx/v5", "v5.7.1"),
    ]


@pytest.mark.parametrize(
    ("path", "text"),
    [("Cargo.toml", "[package]"), ("package.json", "{not json"), ("pyproject.toml", "[x")],
)
def test_parse_manifest_rejects_unknown_or_malformed(path: str, text: str) -> None:
    with pytest.raises(ValueError):
        parse_manifest(path, text, include_dev=False)


def read(cfg: RadarConfig, token: str | None = None):
    async def go():
        async with make_client() as client:
            return await read_dependencies(cfg, client, token)

    return asyncio.run(go())


@respx.mock
def test_read_dependencies_merges_projects_and_applies_ignore() -> None:
    a = respx.get(CONTENTS.format(repo="me/a", path="package.json")).respond(
        200, text='{"dependencies": {"lodash": "^4.17.15", "typescript": "^5.0.0"}}'
    )
    respx.get(CONTENTS.format(repo="me/b", path="web/package.json")).respond(
        200, text='{"dependencies": {"lodash": "^4.17.15"}}'
    )
    respx.get(CONTENTS.format(repo="me/b", path="go.mod")).respond(
        200, text="module x\n\nrequire github.com/spf13/cobra v1.8.0\n"
    )
    cfg = RadarConfig(
        enabled=True,
        manifests=[
            {"repo": "me/a", "paths": ["package.json"]},
            {"repo": "me/b", "paths": ["/web/package.json", "go.mod"]},
        ],
        ignore=["TypeScript"],
    )

    deps, warnings = read(cfg, token="secret-token")

    assert warnings == []
    assert [(d.key, d.version, d.projects) for d in deps] == [
        ("npm:lodash", "4.17.15", ["me/a", "me/b"]),
        ("Go:github.com/spf13/cobra", "v1.8.0", ["me/b"]),
    ]
    request = a.calls.last.request
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert request.headers["Accept"] == "application/vnd.github.raw+json"


@respx.mock
def test_broken_manifest_is_skipped_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    respx.get(CONTENTS.format(repo="me/a", path="package.json")).respond(404)
    respx.get(CONTENTS.format(repo="me/a", path="go.mod")).respond(403)
    respx.get(CONTENTS.format(repo="me/a", path="pyproject.toml")).respond(200, text="[broken")
    respx.get(CONTENTS.format(repo="me/a", path="requirements.txt")).respond(
        200, text="httpx==0.27.0\n"
    )
    cfg = RadarConfig(
        enabled=True,
        manifests=[
            {
                "repo": "me/a",
                "paths": ["package.json", "go.mod", "pyproject.toml", "requirements.txt"],
            }
        ],
    )

    deps, warnings = read(cfg, token="secret-token")

    assert [d.key for d in deps] == ["PyPI:httpx"]
    assert warnings == [
        "me/a:package.json: HTTP 404",
        "me/a:go.mod: HTTP 403",
        "me/a:pyproject.toml: ValueError",
    ]
    assert "secret-token" not in caplog.text


@respx.mock
def test_no_token_sends_no_authorization_header() -> None:
    route = respx.get(CONTENTS.format(repo="me/a", path="go.mod")).respond(200, text="module x\n")
    read(RadarConfig(enabled=True, manifests=[{"repo": "me/a", "paths": ["go.mod"]}]))
    assert "Authorization" not in route.calls.last.request.headers


def test_manifest_repo_must_be_owner_slash_name() -> None:
    with pytest.raises(ValueError):
        RadarConfig(manifests=[{"repo": "just-a-name", "paths": ["go.mod"]}])
