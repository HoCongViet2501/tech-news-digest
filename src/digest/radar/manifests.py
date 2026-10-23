"""Read dependency manifests from GitHub and parse them into Dependency models.

Supported: package.json, requirements*.txt, pyproject.toml and go.mod. The version
kept is the pinned one, or the lower bound of a range (e.g. "^4.17.15" -> 4.17.15).
A manifest that cannot be fetched or parsed is logged and skipped.
"""

import json
import logging
import re
import tomllib
from collections.abc import Iterable
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from digest.config import RadarConfig
from digest.http import get_with_retry
from digest.models import Dependency, Ecosystem
from digest.radar.github import API, github_headers
from digest.radar.versions import numeric

CONTENTS_API = API + "/repos/{repo}/contents/{path}"
RAW = "application/vnd.github.raw+json"
SEMVER = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?")
LOWER_BOUND_OPS = ("==", "===", "~=", ">=")
GO_REQUIRE = re.compile(r"^(\S+)\s+(v\S+)")

log = logging.getLogger(__name__)

Parsed = list[tuple[Ecosystem, str, str | None]]


def _npm_version(spec: str) -> str | None:
    # Git URLs, local paths, aliases and workspace links have no registry version.
    if ":" in spec or "/" in spec:
        return None
    match = SEMVER.search(spec)
    return match.group() if match else None


def parse_package_json(text: str, include_dev: bool) -> Parsed:
    data = json.loads(text)
    sections = ["dependencies"] + (["devDependencies"] if include_dev else [])
    return [
        ("npm", name, _npm_version(str(spec)))
        for section in sections
        for name, spec in (data.get(section) or {}).items()
    ]


def _pep508(lines: Iterable[str]) -> Parsed:
    out: Parsed = []
    for line in lines:
        try:
            req = Requirement(line)
        except InvalidRequirement:
            log.info("radar: skipping unparsable requirement %r", line)
            continue
        if req.url:
            continue
        version = next(
            (s.version.removesuffix(".*") for s in req.specifier if s.operator in LOWER_BOUND_OPS),
            None,
        )
        out.append(("PyPI", canonicalize_name(req.name), version))
    return out


def parse_requirements(text: str) -> Parsed:
    lines = []
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].strip()
        # Options (-r, -e, --index-url ...) and comments carry no package.
        if line and not line.startswith(("#", "-")):
            lines.append(line)
    return _pep508(lines)


def parse_pyproject(text: str, include_dev: bool) -> Parsed:
    data = tomllib.loads(text)
    lines = list(data.get("project", {}).get("dependencies", []))
    if include_dev:
        for group in data.get("dependency-groups", {}).values():
            lines += [entry for entry in group if isinstance(entry, str)]
    return _pep508(lines)


def parse_go_mod(text: str) -> Parsed:
    out: Parsed = []
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
            continue  # only direct dependencies are tracked
        match = GO_REQUIRE.match(line)
        if match:
            version = match.group(2).removeprefix("v").removesuffix("+incompatible")
            out.append(("Go", match.group(1), version))
    return out


def parse_manifest(path: str, text: str, include_dev: bool) -> Parsed:
    """Raises ValueError for an unsupported file name; parse errors propagate."""
    name = PurePosixPath(path).name
    if name == "package.json":
        return parse_package_json(text, include_dev)
    if name == "pyproject.toml":
        return parse_pyproject(text, include_dev)
    if name == "go.mod":
        return parse_go_mod(text)
    if name.startswith("requirements") and name.endswith(".txt"):
        return parse_requirements(text)
    raise ValueError(f"unsupported manifest type: {name}")


async def fetch_manifest(
    client: httpx.AsyncClient, repo: str, path: str, token: str | None
) -> str | None:
    url = CONTENTS_API.format(repo=repo, path=quote(path.lstrip("/")))
    try:
        response = await get_with_retry(client, url, headers=github_headers(token, RAW))
        return response.text
    except Exception as exc:  # wrong path, no permission, network: skip this manifest
        log.warning("radar: cannot read %s:%s (%s)", repo, path, _describe(exc))
        return None


def _describe(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def _lower(a: str | None, b: str | None) -> str | None:
    """The lower of two versions (the one most likely to be affected); unknown loses."""
    if a is None or b is None:
        return a or b
    na, nb = numeric(a), numeric(b)
    if na is None or nb is None:
        return a
    return b if nb < na else a


async def read_dependencies(
    cfg: RadarConfig, client: httpx.AsyncClient, token: str | None
) -> list[Dependency]:
    """Direct dependencies of every configured manifest, merged by ecosystem + name."""
    ignore = {name.lower() for name in cfg.ignore}
    merged: dict[str, Dependency] = {}
    for manifest in cfg.manifests:
        for path in manifest.paths:
            text = await fetch_manifest(client, manifest.repo, path, token)
            if text is None:
                continue
            try:
                parsed = parse_manifest(path, text, cfg.include_dev_dependencies)
            except Exception as exc:
                log.warning("radar: cannot parse %s:%s (%s)", manifest.repo, path, exc)
                continue
            source = f"{manifest.repo}:{path}"
            for ecosystem, name, version in parsed:
                if name.lower() in ignore:
                    continue
                dep = Dependency(ecosystem=ecosystem, name=name, version=version)
                if dep.key in merged:
                    known = merged[dep.key]
                    known.version = _lower(known.version, version)
                    known.manifests.append(source)
                else:
                    dep.manifests.append(source)
                    merged[dep.key] = dep
            log.info("radar: %s has %d dependencies", source, len(parsed))
    return list(merged.values())
