"""In-memory ContentStore for unit tests. Mirrors PostgresStore semantics."""

from collections.abc import Sequence
from datetime import datetime, timedelta

from digest.store.base import DEFAULT_DUE_SLACK, StoreError
from digest.store.models import (
    FetchOutcome,
    NewItem,
    Source,
    SourceSpec,
    Stage,
    StoredItem,
    SyncResult,
    UpsertResult,
)

_SPEC_FIELDS = ("name", "category", "base_weight", "cadence_hours")


class MemoryStore:
    def __init__(self) -> None:
        self._sources: dict[int, Source] = {}
        self._items: dict[str, StoredItem] = {}
        self._next_source_id = 1

    # --- sources -----------------------------------------------------------

    def sync_sources(self, specs: Sequence[SourceSpec]) -> SyncResult:
        by_key = {s.key: s for s in self._sources.values()}
        wanted = {spec.key: spec for spec in specs}
        inserted = updated = disabled = 0
        for key, spec in wanted.items():
            current = by_key.get(key)
            if current is None:
                source = Source(id=self._next_source_id, **spec.model_dump())
                self._sources[source.id] = source
                self._next_source_id += 1
                inserted += 1
                continue
            changes = {f: getattr(spec, f) for f in _SPEC_FIELDS}
            changes["enabled"] = True
            if any(getattr(current, f) != v for f, v in changes.items()):
                self._sources[current.id] = current.model_copy(update=changes)
                updated += 1
        for source in list(self._sources.values()):
            if source.key not in wanted and source.enabled:
                self._sources[source.id] = source.model_copy(update={"enabled": False})
                disabled += 1
        return SyncResult(inserted=inserted, updated=updated, disabled=disabled)

    def list_sources(self, *, enabled_only: bool = False) -> list[Source]:
        return sorted(
            (s for s in self._sources.values() if s.enabled or not enabled_only),
            key=lambda s: s.id,
        )

    def due_sources(self, now: datetime, *, slack: timedelta = DEFAULT_DUE_SLACK) -> list[Source]:
        return [
            s
            for s in self.list_sources(enabled_only=True)
            if s.last_fetched_at is None
            or s.last_fetched_at + timedelta(hours=s.cadence_hours) <= now + slack
        ]

    def record_fetch(self, source_id: int, outcome: FetchOutcome) -> None:
        source = self._sources.get(source_id)
        if source is None:
            raise StoreError(f"unknown source id {source_id}")
        update: dict[str, object] = {"last_fetched_at": outcome.at}
        if outcome.ok:
            update |= {"last_ok_at": outcome.at, "last_error": None}
            if outcome.etag is not None:
                update["etag"] = outcome.etag
            if outcome.last_modified is not None:
                update["last_modified"] = outcome.last_modified
        else:
            update["last_error"] = outcome.error
        self._sources[source_id] = source.model_copy(update=update)

    # --- items -------------------------------------------------------------

    def upsert_items(self, items: Sequence[NewItem]) -> UpsertResult:
        batch = {item.id: item for item in items}
        for item in batch.values():
            if item.source_id not in self._sources:
                raise StoreError(f"unknown source id {item.source_id}")
        new_ids: list[str] = []
        refreshed = 0
        for item in batch.values():
            current = self._items.get(item.id)
            if current is None:
                self._items[item.id] = StoredItem(
                    **item.model_dump(), first_seen_at=item.fetched_at
                )
                new_ids.append(item.id)
            else:
                self._items[item.id] = current.model_copy(
                    update={
                        "native_score": item.native_score,
                        "native_comments": item.native_comments,
                        "fetched_at": item.fetched_at,
                    }
                )
                refreshed += 1
        return UpsertResult(new_ids=new_ids, refreshed=refreshed)

    def get_items(self, ids: Sequence[str]) -> list[StoredItem]:
        return [self._items[i] for i in dict.fromkeys(ids) if i in self._items]

    def pending(self, stage: Stage, limit: int) -> list[StoredItem]:
        todo = [i for i in self._items.values() if getattr(i, stage.column) is None]
        todo.sort(key=lambda i: i.id)
        todo.sort(key=lambda i: i.first_seen_at, reverse=True)
        return todo[: max(limit, 0)]

    def mark_done(self, stage: Stage, ids: Sequence[str], at: datetime) -> int:
        count = 0
        for item_id in dict.fromkeys(ids):
            item = self._items.get(item_id)
            if item is not None:
                self._items[item_id] = item.model_copy(update={stage.column: at})
                count += 1
        return count
