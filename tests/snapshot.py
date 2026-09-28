"""Minimal snapshot helper. Set UPDATE_SNAPSHOTS=1 to rewrite snapshots after review."""

import os
from pathlib import Path

import pytest

SNAPSHOTS = Path(__file__).parent / "snapshots"


def assert_snapshot(name: str, actual: str) -> None:
    path = SNAPSHOTS / name
    if os.environ.get("UPDATE_SNAPSHOTS") == "1" or not path.exists():
        existed = path.exists()
        path.write_text(actual, encoding="utf-8")
        if not existed:
            pytest.fail(f"snapshot {name} created; review it and re-run")
        return
    assert actual == path.read_text(encoding="utf-8"), f"snapshot {name} differs"
