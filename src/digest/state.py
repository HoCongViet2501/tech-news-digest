"""Read/write the JSON state committed under data/."""

import json
from pathlib import Path


class StateError(Exception):
    """Raised when a state file exists but cannot be read."""


def load_seen(path: Path) -> dict[str, str]:
    """Map of item id -> date (YYYY-MM-DD) it was sent. Missing file = nothing sent yet."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise StateError(f"{path} must contain a JSON object")
    return {str(k): str(v) for k, v in data.items()}
