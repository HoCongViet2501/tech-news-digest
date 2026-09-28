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


def test_save_seen_adds_sent_ids_and_prunes_old(tmp_path: Path) -> None:
    from datetime import date

    from digest.state import save_seen

    path = tmp_path / "seen.json"
    previous = {"keep": "2026-08-01", "drop": "2026-07-28", "edge": "2026-07-29"}

    save_seen(path, previous, ["new1", "new2"], date(2026, 9, 27), retention_days=60)

    # 2026-09-27 minus 60 days = 2026-07-29; entries older than that are pruned
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "keep": "2026-08-01",
        "edge": "2026-07-29",
        "new1": "2026-09-27",
        "new2": "2026-09-27",
    }


def test_digest_roundtrip_and_same_day_merge(tmp_path: Path) -> None:
    from datetime import UTC, date, datetime

    from digest.models import Digest, ScoredItem
    from digest.state import load_digests, save_digest

    def it(n: int) -> ScoredItem:
        return ScoredItem(
            id=f"id{n}",
            source="hn",
            title=f"t{n}",
            url=f"https://e.com/{n}",
            score=n,
            published_at=datetime(2026, 9, 27, tzinfo=UTC),
            relevance=1.0,
        )

    save_digest(tmp_path, Digest(date=date(2026, 9, 26), items=[it(1)], stats={"sent": 1}))
    save_digest(tmp_path, Digest(date=date(2026, 9, 27), items=[it(2)], stats={"sent": 1}))
    # a second run on the same day appends instead of losing the first run's items
    save_digest(tmp_path, Digest(date=date(2026, 9, 27), items=[it(3)], stats={"sent": 1}))

    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-09-26.json", "2026-09-27.json"]
    digests = load_digests(tmp_path)
    assert [d.date for d in digests] == [date(2026, 9, 26), date(2026, 9, 27)]
    assert [i.id for i in digests[1].items] == ["id2", "id3"]
    assert digests[1].stats["sent"] == 2


def test_load_digests_from_missing_dir_is_empty(tmp_path: Path) -> None:
    from digest.state import load_digests

    assert load_digests(tmp_path / "nope") == []
