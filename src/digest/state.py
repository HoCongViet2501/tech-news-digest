"""Read/write the JSON state committed under data/."""

import json
import logging
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path

from digest.models import Digest, Vote

log = logging.getLogger(__name__)


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


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def save_seen(
    path: Path,
    seen: dict[str, str],
    sent_ids: Iterable[str],
    run_date: date,
    retention_days: int,
) -> None:
    """Record sent ids under run_date and drop entries older than the retention window."""
    cutoff = (run_date - timedelta(days=retention_days)).isoformat()
    updated = {k: v for k, v in seen.items() if v >= cutoff}
    for item_id in sent_ids:
        updated[item_id] = run_date.isoformat()
    _write_atomic(path, json.dumps(updated, indent=1, sort_keys=False) + "\n")


def save_digest(digests_dir: Path, digest: Digest) -> None:
    """Write digests/YYYY-MM-DD.json; a second run on the same day appends its items."""
    path = digests_dir / f"{digest.date.isoformat()}.json"
    if path.is_file():
        previous = Digest.model_validate_json(path.read_text(encoding="utf-8"))
        known = {i.id for i in previous.items}
        items = previous.items + [i for i in digest.items if i.id not in known]
        known_radar = {e.id for e in previous.radar}
        radar = previous.radar + [e for e in digest.radar if e.id not in known_radar]
        digest = digest.model_copy(
            update={"items": items, "radar": radar, "stats": {**digest.stats, "sent": len(items)}}
        )
    _write_atomic(path, digest.model_dump_json(indent=1) + "\n")


def load_digests(digests_dir: Path) -> list[Digest]:
    """All saved digests, oldest first."""
    if not digests_dir.is_dir():
        return []
    return [
        Digest.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted(digests_dir.glob("*.json"))
    ]


def load_radar_map(path: Path) -> dict[str, str | None]:
    """Package -> GitHub repo cache. A broken cache is only a cache: start over."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("cannot read %s, rebuilding it: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): (str(v) if v else None) for k, v in data.items()}


def save_radar_map(path: Path, repo_map: dict[str, str | None]) -> None:
    _write_atomic(path, json.dumps(dict(sorted(repo_map.items())), indent=1) + "\n")


def load_votes(path: Path) -> list[Vote]:
    """Every vote in feedback.jsonl, in file order; unreadable lines are skipped."""
    if not path.is_file():
        return []
    votes = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            votes.append(Vote.model_validate_json(line))
        except ValueError as exc:
            log.warning("%s:%d skipped: %s", path, n, type(exc).__name__)
    return votes


def latest_votes(votes: Iterable[Vote]) -> dict[str, Vote]:
    """item id -> its most recent vote (highest Telegram update id)."""
    latest: dict[str, Vote] = {}
    for vote in sorted(votes, key=lambda v: v.update_id):
        latest[vote.item_id] = vote
    return latest


def append_votes(path: Path, votes: Iterable[Vote]) -> None:
    lines = "".join(v.model_dump_json() + "\n" for v in votes)
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(lines)


def load_offset(path: Path) -> int | None:
    """Next Telegram update id to fetch; None before the first run."""
    if not path.is_file():
        return None
    try:
        return int(json.loads(path.read_text(encoding="utf-8"))["offset"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise StateError(f"Cannot read {path}: {exc}") from exc


def save_offset(path: Path, offset: int) -> None:
    _write_atomic(path, json.dumps({"offset": offset}) + "\n")
