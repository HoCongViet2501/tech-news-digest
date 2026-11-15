"""ContentStore contract: the same tests run against MemoryStore and PostgresStore.

The postgres variant needs DIGEST_TEST_DB_URL (see conftest.pg_conn); it is skipped
locally without it and fails in CI without it.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from digest.store import (
    ContentStore,
    EnrichmentUpdate,
    FetchOutcome,
    ItemTag,
    MemoryStore,
    NewItem,
    PostgresStore,
    SourceSpec,
    Stage,
    StoreError,
    TagMethod,
    TagSpec,
    TagUpdate,
    load_sources,
)
from digest.taxonomy import load_taxonomy

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


def test_upsert_leaves_items_of_another_source_alone(store: ContentStore) -> None:
    """The same URL on HN and Lobsters: Lobsters' score must not overwrite HN's."""
    store.sync_sources([spec("Alpha"), spec("Beta")])
    alpha, beta = (s.id for s in store.list_sources())
    store.upsert_items([make_item(1, alpha, native_score=10)])

    result = store.upsert_items(
        [
            make_item(1, beta, native_score=99, fetched_at=T0 + timedelta(hours=1)),
            make_item(2, beta),
        ]
    )

    assert (result.new_ids, result.refreshed, result.other_source) == ([f"{2:040x}"], 0, 1)
    [item] = store.get_items([f"{1:040x}"])
    assert (item.source_id, item.native_score, item.fetched_at) == (alpha, 10, T0)


def test_upsert_records_a_mention_per_source(store: ContentStore) -> None:
    store.sync_sources([spec("Alpha"), spec("Beta")])
    alpha, beta = (s.id for s in store.list_sources())
    one = f"{1:040x}"
    store.upsert_items([make_item(1, alpha, native_score=10, discussion_url="https://a/1")])
    later = T0 + timedelta(hours=1)
    store.upsert_items([make_item(1, beta, native_score=99, native_comments=7, fetched_at=later)])
    # Both sources see it again: each refreshes only its own mention.
    store.upsert_items([make_item(1, alpha, native_score=20, fetched_at=later)])
    store.upsert_items(
        [make_item(1, beta, native_score=120, discussion_url="https://b/1", fetched_at=later)]
    )

    mentions = store.get_mentions([one, "f" * 40])

    assert [
        (m.source_id, m.native_score, m.native_comments, m.discussion_url, m.first_seen_at)
        for m in mentions
    ] == [
        # A refresh without a discussion URL keeps the stored one.
        (alpha, 20, 0, "https://a/1", T0),
        (beta, 120, 0, "https://b/1", later),
    ]
    [item] = store.get_items([one])
    assert (item.source_id, item.native_score) == (alpha, 20)


def test_upsert_stores_and_merges_native_tags(store: ContentStore) -> None:
    store.sync_sources([spec("Alpha"), spec("Beta")])
    alpha, beta = (s.id for s in store.list_sources())
    one = f"{1:040x}"
    store.upsert_items([make_item(1, alpha, native_tags=["go", "web"])])
    store.mark_done(Stage.TAG, [one], T0)

    # Nothing new from another source: the item stays tagged.
    store.upsert_items([make_item(1, beta, native_tags=["web"])])
    [item] = store.get_items([one])
    assert (item.native_tags, item.tagged_at) == (["go", "web"], T0)

    # New tags from another source are merged in and the item is tagged again.
    result = store.upsert_items([make_item(1, beta, native_tags=["kafka", "go"])])
    assert result.other_source == 1
    [item] = store.get_items([one])
    assert (item.native_tags, item.tagged_at) == (["go", "web", "kafka"], None)
    assert item.source_id == alpha


def test_native_tags_are_normalized_from_items() -> None:
    from digest.models import Item

    item = Item(
        id="a" * 40,
        source="devto",
        title="t",
        url="https://x.example/",
        score=1,
        published_at=T0,
        tags=["Large Language Models", " Go ", "go", ""],
    )

    assert NewItem.from_item(item, 1, T0).native_tags == ["large-language-models", "go"]


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


def test_sync_sets_created_at(store: ContentStore) -> None:
    store.sync_sources([spec("Alpha")])

    [source] = store.list_sources()

    assert source.created_at is not None


# --- enrichment ------------------------------------------------------------


def test_save_enrichment_fills_fields_and_marks_done(store: ContentStore, source_id: int) -> None:
    store.upsert_items([make_item(1, source_id), make_item(2, source_id, description="feed")])
    done_at = T0 + timedelta(hours=1)

    count = store.save_enrichment(
        [
            EnrichmentUpdate(
                id=f"{1:040x}",
                image_url="https://img.example/1.png",
                description="from page",
                reading_min=4,
                lang="vi",
            ),
            EnrichmentUpdate(id=f"{2:040x}", description="from page", lang="en"),
            EnrichmentUpdate(id="f" * 40, lang="en"),  # unknown id: ignored
        ],
        done_at,
    )

    assert count == 2
    one, two = store.get_items([f"{1:040x}", f"{2:040x}"])
    assert (one.image_url, one.description, one.reading_min, one.lang) == (
        "https://img.example/1.png",
        "from page",
        4,
        "vi",
    )
    # The source's own description wins over the page's.
    assert (two.image_url, two.description, two.reading_min, two.lang) == (
        None,
        "feed",
        None,
        "en",
    )
    assert one.enriched_at == two.enriched_at == done_at
    assert store.pending(Stage.ENRICH, limit=10) == []


def test_save_enrichment_with_nothing_found_still_marks_done(
    store: ContentStore, source_id: int
) -> None:
    store.upsert_items([make_item(1, source_id)])
    store.save_enrichment(
        [EnrichmentUpdate(id=f"{1:040x}", image_url="https://img.example/a.png")], T0
    )

    # A later empty result (e.g. a re-run) neither clears fields nor re-queues the item.
    assert store.save_enrichment([EnrichmentUpdate(id=f"{1:040x}")], T0 + timedelta(1)) == 1

    [item] = store.get_items([f"{1:040x}"])
    assert item.image_url == "https://img.example/a.png"
    assert item.enriched_at == T0 + timedelta(1)
    assert store.save_enrichment([], T0) == 0


# --- pop_pct ---------------------------------------------------------------


def pcts(store: ContentStore, *ns: int) -> list[float | None]:
    return [i.pop_pct for i in store.get_items([f"{n:040x}" for n in ns])]


def test_pop_pct_is_mid_rank_percentile_per_source(store: ContentStore) -> None:
    store.sync_sources([spec("Alpha"), spec("Beta"), spec("Gamma")])
    alpha, beta, gamma = (s.id for s in store.list_sources())
    # Alpha: scores 10, 20, 20, 40 -> ties share the middle of their ranks.
    store.upsert_items(
        [
            make_item(1, alpha, native_score=10),
            make_item(2, alpha, native_score=20),
            make_item(3, alpha, native_score=20),
            make_item(4, alpha, native_score=40),
            # Beta: a lone item. Gamma: all tie (RSS: score = base weight).
            make_item(5, beta, native_score=1000),
            make_item(6, gamma, native_score=60),
            make_item(7, gamma, native_score=60),
        ]
    )

    changed = store.recompute_pop_pct(T0, timedelta(days=7))

    assert changed == 7
    assert pcts(store, 1, 2, 3, 4) == pytest.approx([0, 0.5, 0.5, 1], abs=1e-6)
    assert pcts(store, 5, 6, 7) == pytest.approx([0.5, 0.5, 0.5], abs=1e-6)
    # Nothing changed since: nothing is rewritten.
    assert store.recompute_pop_pct(T0, timedelta(days=7)) == 0


def test_pop_pct_only_counts_the_window(store: ContentStore, source_id: int) -> None:
    store.upsert_items(
        [
            make_item(1, source_id, native_score=5),
            make_item(2, source_id, native_score=50),
            # Published 10 days ago: outside the window, neither ranked nor updated.
            make_item(3, source_id, native_score=500, published_at=T0 - timedelta(days=10)),
        ]
    )

    store.recompute_pop_pct(T0, timedelta(days=7))
    assert pcts(store, 1, 2, 3) == pytest.approx([0, 1, None], abs=1e-6)

    store.upsert_items([make_item(2, source_id, native_score=1)])  # score dropped
    assert store.recompute_pop_pct(T0, timedelta(days=7)) == 2
    assert pcts(store, 1, 2) == pytest.approx([1, 0], abs=1e-6)


# --- tags ------------------------------------------------------------------

NATIVE, RULE, EMBED, LLM = TagMethod.NATIVE, TagMethod.RULE, TagMethod.EMBED, TagMethod.LLM
TAG_STEPS = frozenset({NATIVE, RULE, EMBED})


def tag(slug: str, parent: str | None = None, **kw: object) -> TagSpec:
    data: dict[str, object] = {"slug": slug, "name": slug.title(), "parent": parent}
    return TagSpec.model_validate(data | kw)


TAGS = [tag("backend"), tag("messaging", "backend"), tag("kafka", "messaging", aliases=["k"])]


def test_sync_tags_inserts_updates_and_retires(store: ContentStore) -> None:
    first = store.sync_tags(TAGS)
    again = store.sync_tags(TAGS)
    assert (first.inserted, first.updated, first.disabled) == (3, 0, 0)
    assert not again.changed

    changed = store.sync_tags([TAGS[0], tag("messaging", "backend", description="Queues")])

    assert (changed.inserted, changed.updated, changed.disabled) == (0, 1, 1)
    tags = {t.slug: t for t in store.list_tags()}
    assert tags["kafka"].active is False  # retired, never deleted
    assert tags["messaging"].description == "Queues"
    assert [t.slug for t in store.list_tags(active_only=True)] == ["backend", "messaging"]

    back = store.sync_tags(TAGS)
    assert (back.inserted, back.updated, back.disabled) == (0, 2, 0)
    assert store.list_tags(active_only=True) == store.list_tags()
    assert {t.slug: t.aliases for t in store.list_tags()}["kafka"] == ["k"]


def test_sync_real_taxonomy_is_idempotent(store: ContentStore) -> None:
    specs = load_taxonomy(REPO_ROOT / "taxonomy.yaml").specs()

    first = store.sync_tags(specs)

    assert first.inserted == len(specs) >= 60
    assert not store.sync_tags(specs).changed


def test_sync_tags_rejects_unknown_parent(store: ContentStore) -> None:
    with pytest.raises(StoreError):
        store.sync_tags([tag("kafka", "nowhere")])


def test_save_tags_replaces_only_the_given_methods(store: ContentStore, source_id: int) -> None:
    store.sync_tags(TAGS)
    store.upsert_items([make_item(1, source_id), make_item(2, source_id)])
    one, two = f"{1:040x}", f"{2:040x}"

    store.save_tags(
        [
            TagUpdate(
                id=one,
                methods=TAG_STEPS,
                tags=[ItemTag(tag="backend", confidence=0.7, method=EMBED)],
            ),
            TagUpdate(
                id=two,
                methods=TAG_STEPS,
                tags=[ItemTag(tag="kafka", confidence=0.9, method=RULE)],
                done=True,
            ),
        ],
        T0,
    )
    assert store.pending(Stage.TAG, limit=10) == store.get_items([one])

    # The LLM step replaces embedding guesses with its own tags and sets quality.
    count = store.save_tags(
        [
            TagUpdate(
                id=one,
                methods=frozenset({EMBED, LLM}),
                tags=[ItemTag(tag="messaging", confidence=0.85, method=LLM)],
                quality=0.4,
                done=True,
            )
        ],
        T0 + timedelta(hours=1),
    )

    assert count == 1
    assert [(t.item_id, t.tag, t.method) for t in store.get_item_tags([one, two])] == [
        (one, "messaging", LLM),
        (two, "kafka", RULE),
    ]
    first, second = store.get_items([one, two])
    assert (first.quality, first.tagged_at) == (pytest.approx(0.4), T0 + timedelta(hours=1))
    assert (second.quality, second.tagged_at) == (None, T0)

    # Re-running the first three steps keeps LLM tags; a tag found again by another
    # method keeps the higher confidence.
    store.save_tags(
        [
            TagUpdate(
                id=one,
                methods=TAG_STEPS,
                tags=[
                    ItemTag(tag="kafka", confidence=0.95, method=NATIVE),
                    ItemTag(tag="messaging", confidence=0.6, method=EMBED),
                ],
            )
        ],
        T0,
    )
    assert [(t.tag, t.method) for t in store.get_item_tags([one])] == [
        ("kafka", NATIVE),
        ("messaging", LLM),
    ]
    assert store.get_items([one])[0].quality == pytest.approx(0.4)  # None keeps it


def test_save_tags_rejects_unknown_tag_or_item(store: ContentStore, source_id: int) -> None:
    store.sync_tags(TAGS)
    store.upsert_items([make_item(1, source_id)])
    with pytest.raises(StoreError):
        store.save_tags(
            [
                TagUpdate(
                    id=f"{1:040x}",
                    methods=TAG_STEPS,
                    tags=[ItemTag(tag="nope", confidence=0.9, method=RULE)],
                )
            ],
            T0,
        )
    with pytest.raises(StoreError):
        store.save_tags([TagUpdate(id="f" * 40, methods=TAG_STEPS)], T0)
    assert store.save_tags([], T0) == 0


# --- embeddings ------------------------------------------------------------


def unit(*hot: int) -> list[float]:
    vector = [0.0] * 384
    for i in hot:
        vector[i] = 1 / len(hot) ** 0.5
    return vector


def test_embeddings_round_trip_as_halfvec(store: ContentStore, source_id: int) -> None:
    store.upsert_items([make_item(n, source_id) for n in (1, 2, 3)])
    one, two = f"{1:040x}", f"{2:040x}"
    vector = [((i % 7) - 3) / 10 for i in range(384)]

    count = store.save_embeddings({one: vector, two: unit(5), "f" * 40: unit(1)}, T0)

    assert count == 2
    stored = store.get_embeddings([one, two, f"{3:040x}"])
    assert stored.keys() == {one, two}
    assert stored[one] == pytest.approx(vector, abs=1e-3)
    assert stored[two][5] == pytest.approx(1.0)
    assert [i.id for i in store.pending(Stage.EMBED, limit=10)] == [f"{3:040x}"]
    assert store.get_items([one])[0].embedded_at == T0
    # Only embedded items are ready for tagging.
    assert {i.id for i in store.pending(Stage.TAG, limit=10, after=Stage.EMBED)} == {one, two}
    with pytest.raises(StoreError):
        store.save_embeddings({one: [0.1] * 10}, T0)


# --- clusters --------------------------------------------------------------


def test_recent_items_are_embedded_and_in_the_window(store: ContentStore, source_id: int) -> None:
    store.upsert_items([make_item(n, source_id) for n in (1, 2, 100)])
    store.save_embeddings({f"{n:040x}": unit(n) for n in (1, 100)}, T0)

    recent = store.recent_items(T0 - timedelta(hours=72))

    assert [i.id for i in recent] == [f"{1:040x}"]


def test_merge_cluster_creates_extends_and_merges(store: ContentStore, source_id: int) -> None:
    store.upsert_items(
        [make_item(n, source_id, fetched_at=T0 + timedelta(minutes=n)) for n in range(1, 6)]
    )
    ids = [f"{n:040x}" for n in range(1, 6)]

    a = store.merge_cluster([ids[1], ids[0]])
    b = store.merge_cluster([ids[3], ids[2]])
    assert a != b
    assert store.merge_cluster([ids[0], ids[1]]) == a  # idempotent
    assert [(c.id, c.canonical_item_id, c.item_ids) for c in store.list_clusters()] == [
        (a, ids[0], ids[:2]),
        (b, ids[2], ids[2:4]),
    ]

    # A new item linking both clusters merges them into the older one.
    merged = store.merge_cluster([ids[4], ids[3], ids[1]])

    assert merged == min(a, b)
    [cluster] = store.list_clusters()
    assert (cluster.canonical_item_id, cluster.item_ids) == (ids[0], ids)
    assert {i.cluster_id for i in store.get_items(ids)} == {merged}


def test_merge_cluster_needs_two_known_items(store: ContentStore, source_id: int) -> None:
    store.upsert_items([make_item(1, source_id)])
    with pytest.raises(StoreError):
        store.merge_cluster([f"{1:040x}", "f" * 40])
