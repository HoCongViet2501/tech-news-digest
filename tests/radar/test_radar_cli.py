"""End-to-end radar runs over the offline fixtures (demo manifests, real API responses)."""

import json
from pathlib import Path

import pytest
import yaml

from digest.__main__ import main

REPO_CONFIG = Path(__file__).parents[2] / "config.yaml"
LODASH_IDS = {
    "GHSA-29mw-wpgm-hmr9",
    "GHSA-35jh-r3h4-6jhm",
    "GHSA-f23m-r3pf-42rh",
    "GHSA-p6mc-m468-83gw",
    "GHSA-r5fr-rjxr-66jc",
    "GHSA-xxjr-mmjv-4gpg",
}


def write_config(tmp_path: Path, web_paths: tuple[str, ...] = ("package.json",)) -> Path:
    raw = yaml.safe_load(REPO_CONFIG.read_text(encoding="utf-8"))
    raw["ai"]["enabled"] = False
    raw["radar"] = {
        "enabled": True,
        "manifests": [
            {"repo": "demo/web-app", "paths": list(web_paths)},
            {"repo": "demo/api-service", "paths": ["requirements.txt"]},
        ],
        "ignore": ["eslint"],
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def run(config: Path, data: Path, *extra: str) -> int:
    return main(
        [
            "run",
            "--date",
            "2026-09-27",
            "--config",
            str(config),
            "--data-dir",
            str(data),
            "--site-dir",
            str(data.parent / "site"),
            *extra,
        ]
    )


def test_lodash_vulnerabilities_are_reported_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, data = write_config(tmp_path), tmp_path / "data"

    assert run(config, data) == 0
    first = capsys.readouterr().out
    assert run(config, data) == 0
    second = capsys.readouterr().out

    assert "<b>🛡 Radar thư viện</b>" in first
    assert first.index("Radar thư viện") < first.index("1. <a href=")  # radar comes first
    assert (
        '🔴 <b>lodash</b> 4.17.15 · <a href="https://osv.dev/vulnerability/GHSA-p6mc-m468-83gw">'
        in first
    )
    assert "Prototype Pollution in lodash · đã sửa ở 4.17.19" in first
    assert "Radar" not in second  # nothing new on the second run

    state = json.loads((data / "radar_state.json").read_text(encoding="utf-8"))
    assert set(state["reported"]) >= LODASH_IDS
    assert "CVE-2020-8203" in state["reported"]  # aliases too
    assert set(state["releases"]) == {"lodash/lodash", "encode/httpx"}  # silent baseline
    repo_map = json.loads((data / "radar_map.json").read_text(encoding="utf-8"))
    assert repo_map == {"PyPI:httpx": "encode/httpx", "npm:lodash": "lodash/lodash"}
    digest = json.loads((data / "digests" / "2026-09-27.json").read_text(encoding="utf-8"))
    assert {e["key"] for e in digest["radar"]} == LODASH_IDS
    assert "Radar thư viện" in (data.parent / "site" / "2026-09-27.html").read_text("utf-8")


def test_new_major_is_reported_after_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, data = write_config(tmp_path), tmp_path / "data"
    data.mkdir()
    (data / "radar_state.json").write_text(
        json.dumps(
            {
                "reported": dict.fromkeys(LODASH_IDS, "2026-09-26"),
                "releases": {"encode/httpx": ["0.27.0"]},
            }
        ),
        encoding="utf-8",
    )

    assert run(config, data) == 0

    out = capsys.readouterr().out
    assert (
        '⬆️ <b>httpx</b> 0.27.0 → <a href="https://github.com/encode/httpx/releases/tag/0.28.0">'
        "0.28.0</a> · major mới · <i>api-service</i>"
    ) in out
    assert "lodash" not in out.split("1. <a href=")[0]  # lodash ids were already reported


def test_broken_manifest_does_not_break_the_digest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="digest")
    config = write_config(tmp_path, web_paths=("package.json", "missing/package.json"))
    data = tmp_path / "data"

    assert run(config, data) == 0

    out = capsys.readouterr().out
    assert "1. <a href=" in out and "lodash" in out
    assert "radar_warnings=demo/web-app:missing/package.json: HTTP 404" in caplog.text


def test_dry_run_writes_no_radar_state(tmp_path: Path) -> None:
    data = tmp_path / "data"
    assert run(write_config(tmp_path), data, "--dry-run") == 0
    assert not data.exists()


def test_radar_disabled_by_default_in_repo_config(tmp_path: Path) -> None:
    data = tmp_path / "data"
    assert run(REPO_CONFIG, data) == 0
    assert not (data / "radar_state.json").exists()
