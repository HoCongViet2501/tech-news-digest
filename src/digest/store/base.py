"""The ContentStore interface shared by the Postgres backend and the in-memory test double."""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Protocol

from digest.store.models import (
    Cluster,
    EnrichmentUpdate,
    FetchOutcome,
    Mention,
    NewItem,
    Source,
    SourceSpec,
    Stage,
    StoredItem,
    StoredItemTag,
    SyncResult,
    Tag,
    TagSpec,
    TagUpdate,
    UpsertResult,
)

# Cron runs start with a few minutes of jitter; without slack, a 24 h source fetched at
# 07:00:05 would not be due at 07:00:02 the next day and would slip to the next run.
DEFAULT_DUE_SLACK = timedelta(minutes=15)

EMBEDDING_DIM = 384  # items.embedding is halfvec(384)


class StoreError(Exception):
    """Raised for invalid store calls (e.g. an unknown source id)."""


class ContentStore(Protocol):
    def sync_sources(self, specs: Sequence[SourceSpec]) -> SyncResult:
        """Upsert specs by (kind, url) and enable them; disable every other source."""
        ...

    def list_sources(self, *, enabled_only: bool = False) -> list[Source]: ...

    def due_sources(self, now: datetime, *, slack: timedelta = DEFAULT_DUE_SLACK) -> list[Source]:
        """Enabled sources never fetched, or last fetched >= cadence_hours - slack ago."""
        ...

    def record_fetch(self, source_id: int, outcome: FetchOutcome) -> None:
        """Store fetch health and conditional-GET validators for one source."""
        ...

    def upsert_items(self, items: Sequence[NewItem]) -> UpsertResult:
        """Insert new items; for ids known from the same source refresh only native_score,
        native_comments and fetched_at. Ids known from another source keep the first
        source's numbers (counted in other_source). In every case new native tags are
        merged into items.native_tags (an item whose tags grew loses tagged_at, so it is
        tagged again), and the (item, source) item_mentions row is inserted or refreshed.
        Duplicate ids within the batch: the last one wins."""
        ...

    def get_mentions(self, ids: Sequence[str]) -> list[Mention]:
        """Mentions of the given items, by item id, then first_seen_at, then source id."""
        ...

    def get_items(self, ids: Sequence[str]) -> list[StoredItem]: ...

    def pending(self, stage: Stage, limit: int, *, after: Stage | None = None) -> list[StoredItem]:
        """Items whose stage is not done (and, with `after`, whose `after` stage is done),
        newest first_seen_at first, at most `limit`."""
        ...

    def mark_done(self, stage: Stage, ids: Sequence[str], at: datetime) -> int:
        """Set the stage timestamp for the given ids; returns how many rows were updated."""
        ...

    def save_enrichment(self, updates: Sequence[EnrichmentUpdate], at: datetime) -> int:
        """Write enrichment results and mark those items enriched (even when every field
        is None, so a page that cannot be enriched is not retried). Returns rows updated."""
        ...

    def recompute_pop_pct(self, now: datetime, window: timedelta) -> int:
        """Set pop_pct of every item published at or after `now - window` to the mid-rank
        percentile of its native_score among those items of the same source, in 0-1.
        Ties share a value, so a source whose items all tie (RSS: native_score is the
        base weight) or that has one item gets 0.5. Older items keep their last value.
        Returns how many rows changed."""
        ...

    # --- tags (W2) ---------------------------------------------------------

    def sync_tags(self, specs: Sequence[TagSpec]) -> SyncResult:
        """Upsert tags by slug and activate them; retire (active = false) every other tag.
        Tags are never deleted: item_tags cascade on delete. `disabled` counts retirements."""
        ...

    def list_tags(self, *, active_only: bool = False) -> list[Tag]: ...

    def save_tags(self, updates: Sequence[TagUpdate], at: datetime) -> int:
        """Per item: delete its tags whose method is in `methods`, insert `tags` (a tag
        that is still there from another method keeps the higher confidence), set quality
        when given and tagged_at = at when done. Raises StoreError for an unknown item or
        tag. Returns how many items were updated."""
        ...

    def get_item_tags(self, ids: Sequence[str]) -> list[StoredItemTag]:
        """Tags of the given items, by item id, then confidence (highest first), then tag."""
        ...

    # --- embeddings (W2) ---------------------------------------------------

    def save_embeddings(self, vectors: Mapping[str, Sequence[float]], at: datetime) -> int:
        """Store each vector (EMBEDDING_DIM values, kept as halfvec) and set embedded_at.
        Unknown ids are skipped. Returns rows updated."""
        ...

    def get_embeddings(self, ids: Sequence[str]) -> dict[str, list[float]]:
        """Stored vectors by item id; items without one are left out."""
        ...

    # --- clusters (W2) -----------------------------------------------------

    def recent_items(self, since: datetime) -> list[StoredItem]:
        """Embedded items published at or after `since`, oldest first."""
        ...

    def merge_cluster(self, item_ids: Sequence[str]) -> int:
        """Put the items (at least two) into one cluster, together with every item of the
        clusters they already belong to. The lowest existing cluster id survives (other
        clusters are deleted); with none, a cluster is created. The canonical item is the
        one seen first. Returns the cluster id."""
        ...

    def list_clusters(self) -> list[Cluster]: ...

    # --- TL;DR (W3) --------------------------------------------------------

    def tldr_candidates(self, as_of: datetime, pool_size: int) -> list[StoredItem]:
        """Items among the top `pool_size` of the Popular feed at `as_of` (one per
        cluster: its canonical item) that have neither a tldr nor summarized_at, in rank
        order. Postgres reads the ranking from public.popular_feed('popular'), so it is
        never duplicated in Python; MemoryStore uses a stand-in order (see there)."""
        ...

    def save_tldrs(self, tldrs: Mapping[str, str], at: datetime) -> int:
        """Store each TL;DR and set summarized_at. Unknown ids are skipped. Returns rows
        updated. Items skipped without a TL;DR use mark_done(Stage.SUMMARIZE, ...)."""
        ...
