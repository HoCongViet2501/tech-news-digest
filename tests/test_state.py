import json
from pathlib import Path

import pytest

from digest.state import StateError, load_seen


def test_missing_seen_file_is_empty(tmp_path: Path) -> None:
    assert load_seen(tmp_path / "seen.json") == {}


def test_loads_seen_map(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    path.write_text(json.dumps({"abc": "2026-09-01"}), encoding="utf-8")

    assert load_seen(path) == {"abc": "2026-09-01"}


def test_corrupt_seen_file_fails_loudly(tmp_path: Path) -> None:
    # Silently treating it as empty would resend 60 days of items.
    path = tmp_path / "seen.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(StateError, match="seen.json"):
        load_seen(path)
