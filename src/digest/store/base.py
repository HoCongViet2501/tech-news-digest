"""The ContentStore interface shared by the Postgres backend and the in-memory test double."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from digest.store.models import (
    EnrichmentUpdate,
    FetchOutcome,
    NewItem,
    Source,
    SourceSpec,
    Stage,
    StoredItem,
    SyncResult,
    UpsertResult,
)

# Cron runs start with a few minutes of jitter; without slack, a 24 h source fetched at
# 07:00:05 would not be due at 07:00:02 the next day and would slip to the next run.
DEFAULT_DUE_SLACK = timedelta(minutes=15)


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
        native_comments and fetched_at. Ids known from another source are left untouched
        (counted in other_source). Duplicate ids within the batch: the last one wins."""
        ...

    def get_items(self, ids: Sequence[str]) -> list[StoredItem]: ...

    def pending(self, stage: Stage, limit: int) -> list[StoredItem]:
        """Items whose stage is not done, newest first_seen_at first, at most `limit`."""
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
