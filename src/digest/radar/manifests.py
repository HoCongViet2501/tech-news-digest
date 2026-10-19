"""Read manifests through the GitHub Contents API and parse their direct dependencies.

Supported: package.json, requirements*.txt, pyproject.toml (PEP 621 and dependency
groups) and go.mod. Ranges are reduced to their lower bound ("^4.17.15" -> 4.17.15),
which is the version OSV is queried with; unpinned dependencies get version None.
"""

import json
import logging
import posixpath
import re
import tomllib

import httpx

from digest.config import RadarConfig
from digest.http import get_with_retry
from digest.models import Dependency, Ecosystem
from digest.radar import github

CONTENTS = github.API + "/repos/{repo}/contents/{path}"
RAW = "application/vnd.github.raw+json"

VERSION = re.compile(r"\d+(?:\.\d+){0,2}(?:[-+][0-9A-Za-z.-]+)?")
NPM_NON_REGISTRY = ("file:", "link:", "workspace:", "git", "http:", "https:", "github:", "npm:")
PEP508 = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*([^;@]*)")
PIN = re.compile(r"^(==|>=|~=)\s*([0-9][0-9A-Za-z.+-]*)$")
GO_REQUIRE = re.compile(r"^(\S+)\s+(v\S+)")

log = logging.getLogger(__name__)


def normalize_pypi(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _npm_version(spec: str) -> str | None:
    spec = spec.strip()
    if not spec or spec.startswith(NPM_NON_REGISTRY):
        return None
    match = VERSION.search(spec.split("||")[0])
    if not match:
        return None
    version = match.group(0)
    core = re.split(r"[-+]", version, maxsplit=1)[0]
    # "1.2" / "1.2.x" -> 1.2.0, so OSV gets a full semver
    return core + ".0" * (2 - core.count(".")) + version[len(core) :]


def parse_package_json(text: str, include_dev: bool) -> list[tuple[str, str | None]]:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("package.json must be a JSON object")
    sections = ["dependencies"] + (["devDependencies"] if include_dev else [])
    deps: list[tuple[str, str | None]] = []
    for section in sections:
        for name, spec in (data.get(section) or {}).items():
            deps.append((name, _npm_version(spec) if isinstance(spec, str) else None))
    return deps


def _pep508(requirement: str) -> tuple[str, str | None] | None:
    match = PEP508.match(requirement)
    if not match:
        return None
    name, spec = match.group(1), match.group(2).strip()
    pins = [PIN.match(part.strip()) for part in spec.split(",") if part.strip()]
    version = None
    for op in ("==", ">=", "~="):
        found = [p.group(2) for p in pins if p and p.group(1) == op and "*" not in p.group(2)]
        if found:
            version = found[0]
            break
    return normalize_pypi(name), version


def parse_requirements(text: str) -> list[tuple[str, str | None]]:
    deps = []
    for raw in text.splitlines():
        line = raw.split(" #")[0].strip()
        if not line or line.startswith(("#", "-", "git+", "http:", "https:", "file:")):
            continue
        parsed = _pep508(line)
        if parsed:
            deps.append(parsed)
    return deps


def parse_pyproject(text: str, include_dev: bool) -> list[tuple[str, str | None]]:
    data = tomllib.loads(text)
    project = data.get("project") or {}
    requirements: list[str] = list(project.get("dependencies") or [])
    if include_dev:
        for group in (project.get("optional-dependencies") or {}).values():
            requirements.extend(group)
        for group in (data.get("dependency-groups") or {}).values():
            requirements.extend(r for r in group if isinstance(r, str))  # skip include-group
    return [d for d in (_pep508(r) for r in requirements) if d]


def parse_go_mod(text: str) -> list[tuple[str, str | None]]:
    """Direct requirements only: lines marked `// indirect` are skipped."""
    deps = []
    in_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        if not in_block:
            if not line.startswith("require "):
                continue
            line = line.removeprefix("require ").strip()
        if "// indirect" in line:
            continue
        match = GO_REQUIRE.match(line)
        if match:
            deps.append((match.group(1), match.group(2)))
    return deps


def parse_manifest(path: str, text: str, include_dev: bool) -> tuple[Ecosystem, list]:
    """(ecosystem, [(name, version)]); raises ValueError for unknown or malformed files."""
    name = posixpath.basename(path)
    try:
        if name == "package.json":
            return "npm", parse_package_json(text, include_dev)
        if name == "pyproject.toml":
            return "PyPI", parse_pyproject(text, include_dev)
        if name.startswith("requirements") and name.endswith(".txt"):
            return "PyPI", parse_requirements(text)
        if name == "go.mod":
            return "Go", parse_go_mod(text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot parse {name}: {exc}") from exc
    raise ValueError(f"unsupported manifest type: {name}")


async def read_dependencies(
    cfg: RadarConfig, client: httpx.AsyncClient, token: str | None
) -> tuple[list[Dependency], list[str]]:
    """All direct dependencies across manifests, merged by (ecosystem, name, version).

    Never raises: an unreadable or unparsable manifest is skipped and reported in
    the returned warnings.
    """
    ignore = {n.lower() for n in cfg.ignore}
    merged: dict[tuple[str, str, str | None], Dependency] = {}
    warnings: list[str] = []
    for manifest in cfg.manifests:
        for path in manifest.paths:
            where = f"{manifest.repo}:{path}"
            try:
                response = await get_with_retry(
                    client,
                    CONTENTS.format(repo=manifest.repo, path=path.lstrip("/")),
                    headers=github.headers(token, accept=RAW),
                )
                ecosystem, deps = parse_manifest(path, response.text, cfg.include_dev_dependencies)
            except httpx.HTTPStatusError as exc:
                status = github.describe_status(exc.response)
                log.warning("radar: cannot read %s (%s)", where, status)
                warnings.append(f"{where}: {status}")
                continue
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("radar: cannot read %s (%s)", where, exc)
                warnings.append(f"{where}: {type(exc).__name__}")
                continue
            for name, version in deps:
                if name.lower() in ignore:
                    continue
                dep = merged.setdefault(
                    (ecosystem, name, version),
                    Dependency(ecosystem=ecosystem, name=name, version=version),
                )
                if manifest.repo not in dep.projects:
                    dep.projects.append(manifest.repo)
    return list(merged.values()), warnings
