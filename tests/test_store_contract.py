"""ContentStore contract: the same tests run against MemoryStore and PostgresStore.

The postgres variant needs DIGEST_TEST_DB_URL (see conftest.pg_conn); it is skipped
locally without it and fails in CI without it.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from digest.store import (
    ContentStore,
    FetchOutcome,
    MemoryStore,
    NewItem,
    PostgresStore,
    SourceSpec,
    Stage,
    StoreError,
    load_sources,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 11, 15, 7, 0, tzinfo=UTC)


@pytest.fixture(
    params=[
        pytest.param("memory"),
        pytest.param("postgres", marks=pytest.mark.integration),
    ]
)
def store(request: pytest.FixtureRequest) -> ContentStore:
    if request.param == "memory":
        return MemoryStore()
    return PostgresStore(request.getfixturevalue("pg_conn"))


def spec(name: str, **kw: object) -> SourceSpec:
    data: dict[str, object] = {
        "kind": "rss",
        "name": name,
        "url": f"https://{name.lower()}.example/feed",
    }
    return SourceSpec.model_validate(data | kw)


def make_item(n: int, source_id: int, **kw: object) -> NewItem:
    data: dict[str, object] = {
        "id": f"{n:040x}",
        "source_id": source_id,
        "title": f"Item {n}",
        "url": f"https://example.com/{n}",
        "published_at": T0 - timedelta(hours=n),
        "fetched_at": T0,
        "native_score": n,
    }
    return NewItem.model_validate(data | kw)


@pytest.fixture
def source_id(store: ContentStore) -> int:
    store.sync_sources([spec("Alpha")])
    return store.list_sources()[0].id


# --- sources ---------------------------------------------------------------


def test_sync_inserts_then_second_sync_changes_nothing(store: ContentStore) -> None:
    specs = [spec("Alpha"), spec("Beta", kind="devto", base_weight=70, cadence_hours=3)]

    first = store.sync_sources(specs)
    second = store.sync_sources(specs)

    assert (first.inserted, first.updated, first.disabled) == (2, 0, 0)
    assert not second.changed
    rows = store.list_sources()
    assert [(s.kind, s.name, s.base_weight, s.cadence_hours, s.enabled) for s in rows] == [
        ("rss", "Alpha", 50, 24, True),
        ("devto", "Beta", 70, 3, True),
    ]


def test_sync_updates_disables_and_reenables(store: ContentStore) -> None:
    store.sync_sources([spec("Alpha"), spec("Beta")])
    ids = {s.name: s.id for s in store.list_sources()}

    changed = store.sync_sources([spec("Alpha", base_weight=90)])
    assert (changed.inserted, changed.updated, changed.disabled) == (0, 1, 1)
    by_name = {s.name: s for s in store.list_sources()}
    assert by_name["Alpha"].base_weight == 90
    assert by_name["Beta"].enabled is False
    assert [s.name for s in store.list_sources(enabled_only=True)] == ["Alpha"]

    back = store.sync_sources([spec("Alpha", base_weight=90), spec("Beta")])
    assert (back.inserted, back.updated, back.disabled) == (0, 1, 0)
    # Natural key (kind, url) keeps ids stable across syncs.
    assert {s.name: s.id for s in store.list_sources()} == ids


def test_sync_renamed_source_keeps_its_row(store: ContentStore) -> None:
    store.sync_sources([spec("Alpha")])
    before = store.list_sources()[0]

    renamed = spec("Alpha").model_copy(update={"name": "Alpha Blog"})
    result = store.sync_sources([renamed])

    assert (result.inserted, result.updated) == (0, 1)
    after = store.list_sources()[0]
    assert (after.id, after.name) == (before.id, "Alpha Blog")


def test_sync_real_sources_yaml_is_idempotent(store: ContentStore) -> None:
    specs = load_sources(REPO_ROOT / "sources.yaml").specs()

    first = store.sync_sources(specs)
    second = store.sync_sources(specs)

    assert first.inserted == len(specs)
    assert not second.changed
    assert len(store.list_sources(enabled_only=True)) == len(specs)


def test_record_fetch_ok_then_error_keeps_validators(store: ContentStore, source_id: int) -> None:
    store.record_fetch(
        source_id, FetchOutcome(at=T0, ok=True, etag='"v1"', last_modified="Sat, 14 Nov 2026")
    )
    # 304 without validators: keep the stored ones.
    store.record_fetch(source_id, FetchOutcome(at=T0 + timedelta(hours=1), ok=True))
    store.record_fetch(
        source_id, FetchOutcome(at=T0 + timedelta(hours=2), ok=False, error="HTTP 503")
    )

    source = store.list_sources()[0]
    assert source.etag == '"v1"'
    assert source.last_modified == "Sat, 14 Nov 2026"
    assert source.last_fetched_at == T0 + timedelta(hours=2)
    assert source.last_ok_at == T0 + timedelta(hours=1)
    assert source.last_error == "HTTP 503"

    store.record_fetch(source_id, FetchOutcome(at=T0 + timedelta(hours=3), ok=True, etag='"v2"'))
    source = store.list_sources()[0]
    assert (source.etag, source.last_error) == ('"v2"', None)
    assert source.last_ok_at == T0 + timedelta(hours=3)


def test_record_fetch_unknown_source_raises(store: ContentStore, source_id: int) -> None:
    with pytest.raises(StoreError):
        store.record_fetch(source_id + 1000, FetchOutcome(at=T0, ok=True))


def test_due_sources_follow_cadence(store: ContentStore) -> None:
    store.sync_sources([spec("Daily"), spec("Often", cadence_hours=3), spec("Never"), spec("Gone")])
    store.sync_sources([spec("Daily"), spec("Often", cadence_hours=3), spec("Never")])
    ids = {s.name: s.id for s in store.list_sources()}
    store.record_fetch(ids["Daily"], FetchOutcome(at=T0, ok=True))
    store.record_fetch(ids["Often"], FetchOutcome(at=T0, ok=False, error="timeout"))

    def due(now: datetime, **kw: object) -> list[str]:
        return [s.name for s in store.due_sources(now, **kw)]  # type: ignore[arg-type]

    # Never-fetched sources are due; disabled ones never are.
    assert due(T0 + timedelta(hours=1)) == ["Never"]
    # Cron jitter: a run a few minutes "early" still counts (default slack 15 min).
    assert due(T0 + timedelta(hours=2, minutes=50)) == ["Often", "Never"]
    assert due(T0 + timedelta(hours=2, minutes=50), slack=timedelta(0)) == ["Never"]
    assert due(T0 + timedelta(hours=24)) == ["Daily", "Often", "Never"]


# --- items -----------------------------------------------------------------


def test_upsert_twice_creates_no_duplicates(store: ContentStore, source_id: int) -> None:
    batch = [make_item(n, source_id) for n in range(1, 4)]

    first = store.upsert_items(batch)
    second = store.upsert_items(batch)

    assert first.new_ids == [i.id for i in batch]
    assert (second.new_ids, second.refreshed) == ([], 3)
    assert len(store.pending(Stage.ENRICH, limit=100)) == 3


def test_upsert_refreshes_only_popularity_and_fetched_at(
    store: ContentStore, source_id: int
) -> None:
    store.upsert_items([make_item(1, source_id, description="first")])
    later = T0 + timedelta(hours=3)

    result = store.upsert_items(
        [
            make_item(
                1,
                source_id,
                title="Changed title",
                description="changed",
                native_score=500,
                native_comments=42,
                fetched_at=later,
            ),
            make_item(2, source_id, fetched_at=later),
        ]
    )

    assert result.new_ids == [f"{2:040x}"]
    assert result.refreshed == 1
    old, new = store.get_items([f"{1:040x}", f"{2:040x}"])
    assert (old.title, old.description) == ("Item 1", "first")
    assert (old.native_score, old.native_comments, old.fetched_at) == (500, 42, later)
    assert old.first_seen_at == T0
    assert new.first_seen_at == later


def test_upsert_duplicate_ids_in_batch_last_wins(store: ContentStore, source_id: int) -> None:
    result = store.upsert_items(
        [make_item(1, source_id, native_score=1), make_item(1, source_id, native_score=9)]
    )

    assert result.new_ids == [f"{1:040x}"]
    assert [i.native_score for i in store.get_items([f"{1:040x}"])] == [9]


def test_upsert_empty_batch(store: ContentStore) -> None:
    result = store.upsert_items([])
    assert (result.new_ids, result.refreshed) == ([], 0)


def test_upsert_unknown_source_raises(store: ContentStore, source_id: int) -> None:
    with pytest.raises(StoreError):
        store.upsert_items([make_item(1, source_id + 1000)])


def test_pending_newest_first_with_limit(store: ContentStore, source_id: int) -> None:
    for n in range(1, 5):
        store.upsert_items([make_item(n, source_id, fetched_at=T0 + timedelta(minutes=n))])

    pending = store.pending(Stage.TAG, limit=2)

    assert [i.id for i in pending] == [f"{4:040x}", f"{3:040x}"]
    assert store.pending(Stage.TAG, limit=0) == []


def test_mark_done_removes_from_that_stage_only(store: ContentStore, source_id: int) -> None:
    store.upsert_items([make_item(n, source_id) for n in range(1, 4)])
    done_at = T0 + timedelta(hours=1)

    count = store.mark_done(Stage.ENRICH, [f"{1:040x}", f"{2:040x}", "f" * 40], done_at)

    assert count == 2
    assert [i.id for i in store.pending(Stage.ENRICH, limit=10)] == [f"{3:040x}"]
    assert len(store.pending(Stage.EMBED, limit=10)) == 3
    assert store.get_items([f"{1:040x}"])[0].enriched_at == done_at


def test_killed_run_is_finished_by_next_run(store: ContentStore, source_id: int) -> None:
    """A rerun with the same batch re-queues nothing and loses no pending work."""
    batch = [make_item(n, source_id) for n in range(1, 6)]
    store.upsert_items(batch)
    first_run = store.pending(Stage.ENRICH, limit=2)
    store.mark_done(Stage.ENRICH, [i.id for i in first_run], T0)
    # ...crash. The next run fetches the same items again.
    assert store.upsert_items(batch).new_ids == []

    rest = store.pending(Stage.ENRICH, limit=100)

    assert {i.id for i in rest} == {i.id for i in batch} - {i.id for i in first_run}
