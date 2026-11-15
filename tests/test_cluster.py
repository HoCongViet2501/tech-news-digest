from datetime import UTC, datetime, timedelta

import pytest

from digest.cluster import cluster_recent
from digest.config import ClusterConfig
from digest.embedding import HashEmbedder
from digest.store import MemoryStore, NewItem, SourceSpec

T0 = datetime(2026, 11, 15, 7, 0, tzinfo=UTC)
CFG = ClusterConfig()


@pytest.fixture
def store() -> MemoryStore:
    store = MemoryStore()
    store.sync_sources(
        [SourceSpec(kind="rss", name=n, url=f"https://{n}.example/feed") for n in ("a", "b", "c")]
    )
    return store


def add(store: MemoryStore, n: int, source: str, title: str, hours_ago: float = 1) -> str:
    source_id = {s.name: s.id for s in store.list_sources()}[source]
    item = NewItem(
        id=f"{n:040x}",
        source_id=source_id,
        title=title,
        url=f"https://example.com/{n}",
        published_at=T0 - timedelta(hours=hours_ago),
        fetched_at=T0 - timedelta(hours=hours_ago) + timedelta(minutes=n),
    )
    store.upsert_items([item])
    vector = HashEmbedder().embed([title])[0]
    store.save_embeddings({item.id: vector.tolist()}, T0)
    return item.id


def test_same_story_on_two_sources_is_one_cluster(store: MemoryStore) -> None:
    hn = add(store, 1, "a", "Gemini 4 Hydrogen is out")
    lobsters = add(store, 2, "b", "Gemini 4 Hydrogen is out!")
    add(store, 3, "c", "Something else entirely")

    assert cluster_recent(store, HashEmbedder(), CFG, now=T0) == 1

    [cluster] = store.list_clusters()
    assert (cluster.canonical_item_id, cluster.item_ids) == (hn, [hn, lobsters])
    # Idempotent: a second run changes nothing.
    assert cluster_recent(store, HashEmbedder(), CFG, now=T0 + timedelta(hours=3)) == 0
    assert store.list_clusters() == [cluster]


def test_a_later_duplicate_joins_the_cluster(store: MemoryStore) -> None:
    first = add(store, 1, "a", "Postgres 19 released")
    second = add(store, 2, "b", "Postgres 19 released")
    cluster_recent(store, HashEmbedder(), CFG, now=T0)

    third = add(store, 3, "c", "Postgres 19 released", hours_ago=0.5)

    assert cluster_recent(store, HashEmbedder(), CFG, now=T0) == 1
    [cluster] = store.list_clusters()
    assert cluster.item_ids == [first, second, third]


@pytest.mark.parametrize(
    ("source_b", "title_b", "hours_ago_b"),
    [
        ("a", "Security updates for Friday", 1),  # same source: a series, not a duplicate
        ("b", "Security updates for Friday", 80),  # outside the 72 h window
        ("b", "Node.js 22.23.5 (LTS) Security updates for Friday", 1),  # other version
    ],
)
def test_guards(store: MemoryStore, source_b: str, title_b: str, hours_ago_b: float) -> None:
    title_a = "Security updates for Friday"
    if title_b.startswith("Node"):
        title_a = "Node.js 26.13.1 (LTS) Security updates for Friday"
    add(store, 1, "a", title_a)
    add(store, 2, source_b, title_b, hours_ago=hours_ago_b)

    assert cluster_recent(store, HashEmbedder(), CFG, now=T0) == 0
    assert store.list_clusters() == []
