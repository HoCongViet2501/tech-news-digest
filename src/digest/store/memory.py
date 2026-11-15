"""In-memory ContentStore for unit tests. Mirrors PostgresStore semantics."""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import numpy as np

from digest.config import RankingConfig
from digest.store.base import DEFAULT_DUE_SLACK, EMBEDDING_DIM, StoreError
from digest.store.models import (
    Cluster,
    EnrichmentUpdate,
    FetchOutcome,
    Mention,
    NewItem,
    PendingProfile,
    PruneResult,
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

_SPEC_FIELDS = ("name", "category", "base_weight", "cadence_hours")
POPULAR_WINDOW = timedelta(days=7)  # public.popular_feed's pool


@dataclass
class MemoryProfile:
    """A public.profiles row, reduced to what the pipeline reads and writes."""

    text: str | None
    updated_at: datetime
    embedded_at: datetime | None = None
    user_vec: list[float] | None = None


class MemoryStore:
    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._sources: dict[int, Source] = {}
        self._items: dict[str, StoredItem] = {}
        self._mentions: dict[tuple[str, int], Mention] = {}
        self._tags: dict[str, Tag] = {}
        self._item_tags: dict[tuple[str, str], StoredItemTag] = {}
        self._embeddings: dict[str, list[float]] = {}
        self._clusters: dict[int, Cluster] = {}
        # W4 user data. Tests fill these directly: follow_centroids stands in for
        # public.follow_centroid(), impressions / feed_snapshots hold created_at values.
        self.profiles: dict[UUID, MemoryProfile] = {}
        self.follow_centroids: dict[UUID, list[float]] = {}
        self.impressions: list[datetime] = []
        self.feed_snapshots: list[datetime] = []
        self.ranking = RankingConfig()  # the migration seeds the row with the defaults
        self._next_cluster_id = 1
        self._next_source_id = 1
        self._clock = clock  # stands in for the created_at default of now()

    # --- sources -----------------------------------------------------------

    def sync_sources(self, specs: Sequence[SourceSpec]) -> SyncResult:
        by_key = {s.key: s for s in self._sources.values()}
        wanted = {spec.key: spec for spec in specs}
        inserted = updated = disabled = 0
        for key, spec in wanted.items():
            current = by_key.get(key)
            if current is None:
                source = Source(
                    id=self._next_source_id, created_at=self._clock(), **spec.model_dump()
                )
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
        refreshed = other_source = 0
        for item in batch.values():
            current = self._items.get(item.id)
            if current is None:
                self._items[item.id] = StoredItem(
                    **item.model_dump(), first_seen_at=item.fetched_at
                )
                new_ids.append(item.id)
            else:
                update: dict[str, object] = {}
                added = [t for t in item.native_tags if t not in current.native_tags]
                if added:
                    update |= {"native_tags": [*current.native_tags, *added], "tagged_at": None}
                if current.source_id == item.source_id:
                    update |= {
                        "native_score": item.native_score,
                        "native_comments": item.native_comments,
                        "fetched_at": item.fetched_at,
                    }
                    refreshed += 1
                else:
                    other_source += 1
                if update:
                    self._items[item.id] = current.model_copy(update=update)
            self._mention(item)
        return UpsertResult(new_ids=new_ids, refreshed=refreshed, other_source=other_source)

    def _mention(self, item: NewItem) -> None:
        key = (item.id, item.source_id)
        current = self._mentions.get(key)
        self._mentions[key] = Mention(
            item_id=item.id,
            source_id=item.source_id,
            discussion_url=item.discussion_url
            or (current.discussion_url if current is not None else None),
            native_score=item.native_score,
            native_comments=item.native_comments,
            first_seen_at=current.first_seen_at if current is not None else item.fetched_at,
        )

    def get_mentions(self, ids: Sequence[str]) -> list[Mention]:
        wanted = set(ids)
        found = [m for m in self._mentions.values() if m.item_id in wanted]
        return sorted(found, key=lambda m: (m.item_id, m.first_seen_at, m.source_id))

    def get_items(self, ids: Sequence[str]) -> list[StoredItem]:
        return [self._items[i] for i in dict.fromkeys(ids) if i in self._items]

    def pending(self, stage: Stage, limit: int, *, after: Stage | None = None) -> list[StoredItem]:
        todo = [
            i
            for i in self._items.values()
            if getattr(i, stage.column) is None
            and (after is None or getattr(i, after.column) is not None)
        ]
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

    def save_enrichment(self, updates: Sequence[EnrichmentUpdate], at: datetime) -> int:
        batch = {u.id: u for u in updates}
        count = 0
        for item_id, u in batch.items():
            item = self._items.get(item_id)
            if item is None:
                continue
            update: dict[str, object] = {"enriched_at": at}
            for field in ("image_url", "reading_min", "lang"):
                if getattr(u, field) is not None:
                    update[field] = getattr(u, field)
            if item.description is None:
                update["description"] = u.description
            self._items[item_id] = item.model_copy(update=update)
            count += 1
        return count

    def recompute_pop_pct(self, now: datetime, window: timedelta) -> int:
        by_source: defaultdict[int, list[StoredItem]] = defaultdict(list)
        for item in self._items.values():
            if item.published_at >= now - window:
                by_source[item.source_id].append(item)
        changed = 0
        for items in by_source.values():
            scores = sorted(i.native_score for i in items)
            n = len(scores)
            for item in items:
                if n == 1:
                    pct = 0.5
                else:
                    below = bisect_left(scores, item.native_score)
                    at_or_below = bisect_right(scores, item.native_score)
                    pct = (below + at_or_below - 1) / (2 * (n - 1))
                if item.pop_pct is None or abs(item.pop_pct - pct) > 1e-6:
                    self._items[item.id] = item.model_copy(update={"pop_pct": pct})
                    changed += 1
        return changed

    # --- ranking config ----------------------------------------------------

    def sync_ranking(self, cfg: RankingConfig) -> bool:
        changed = cfg != self.ranking
        self.ranking = cfg.model_copy()
        return changed

    # --- tags --------------------------------------------------------------

    def sync_tags(self, specs: Sequence[TagSpec]) -> SyncResult:
        wanted = {spec.slug: spec for spec in specs}
        for spec in wanted.values():
            if spec.parent is not None and spec.parent not in wanted:
                raise StoreError(f"tag {spec.slug}: unknown parent {spec.parent}")
        inserted = updated = disabled = 0
        for slug, spec in wanted.items():
            tag = Tag(**spec.model_dump(), active=True)
            current = self._tags.get(slug)
            if current is None:
                inserted += 1
            elif current != tag:
                updated += 1
            self._tags[slug] = tag
        for slug, tag in list(self._tags.items()):
            if slug not in wanted and tag.active:
                self._tags[slug] = tag.model_copy(update={"active": False})
                disabled += 1
        return SyncResult(inserted=inserted, updated=updated, disabled=disabled)

    def list_tags(self, *, active_only: bool = False) -> list[Tag]:
        return sorted(
            (t for t in self._tags.values() if t.active or not active_only), key=lambda t: t.slug
        )

    def save_tags(self, updates: Sequence[TagUpdate], at: datetime) -> int:
        batch = {u.id: u for u in updates}
        for u in batch.values():
            if u.id not in self._items:
                raise StoreError(f"unknown item id {u.id}")
            unknown = [t.tag for t in u.tags if t.tag not in self._tags]
            if unknown:
                raise StoreError(f"unknown tags {unknown}")
        for u in batch.values():
            for key, row in list(self._item_tags.items()):
                if row.item_id == u.id and row.method in u.methods:
                    del self._item_tags[key]
            for tag in u.tags:
                key = (u.id, tag.tag)
                current = self._item_tags.get(key)
                if current is None or tag.confidence > current.confidence:
                    self._item_tags[key] = StoredItemTag(item_id=u.id, **tag.model_dump())
            item = self._items[u.id]
            update: dict[str, object] = {}
            if u.quality is not None:
                update["quality"] = u.quality
            if u.done:
                update["tagged_at"] = at
            if update:
                self._items[u.id] = item.model_copy(update=update)
        return len(batch)

    def get_item_tags(self, ids: Sequence[str]) -> list[StoredItemTag]:
        wanted = set(ids)
        found = [t for t in self._item_tags.values() if t.item_id in wanted]
        return sorted(found, key=lambda t: (t.item_id, -t.confidence, t.tag))

    # --- embeddings --------------------------------------------------------

    def save_embeddings(self, vectors: Mapping[str, Sequence[float]], at: datetime) -> int:
        count = 0
        for item_id, vector in vectors.items():
            item = self._items.get(item_id)
            if item is None:
                continue
            if len(vector) != EMBEDDING_DIM:
                raise StoreError(f"embedding of {item_id} has {len(vector)} dims")
            # halfvec storage: round-trip through float16 like Postgres does.
            self._embeddings[item_id] = [float(v) for v in np.float16(vector)]
            self._items[item_id] = item.model_copy(update={"embedded_at": at})
            count += 1
        return count

    def get_embeddings(self, ids: Sequence[str]) -> dict[str, list[float]]:
        return {i: self._embeddings[i] for i in dict.fromkeys(ids) if i in self._embeddings}

    # --- clusters ----------------------------------------------------------

    def recent_items(self, since: datetime) -> list[StoredItem]:
        found = [
            i for i in self._items.values() if i.published_at >= since and i.embedded_at is not None
        ]
        return sorted(found, key=lambda i: (i.published_at, i.id))

    def merge_cluster(self, item_ids: Sequence[str]) -> int:
        members = [self._items[i] for i in dict.fromkeys(item_ids) if i in self._items]
        if len(members) < 2:
            raise StoreError("a cluster needs at least two known items")
        existing = sorted({m.cluster_id for m in members if m.cluster_id is not None})
        ids = {m.id for m in members}
        for cluster_id in existing:
            ids.update(self._clusters[cluster_id].item_ids)
        if existing:
            target = existing[0]
            for cluster_id in existing[1:]:
                del self._clusters[cluster_id]
        else:
            target = self._next_cluster_id
            self._next_cluster_id += 1
        items = sorted((self._items[i] for i in ids), key=lambda i: (i.first_seen_at, i.id))
        self._clusters[target] = Cluster(
            id=target, canonical_item_id=items[0].id, item_ids=[i.id for i in items]
        )
        for item in items:
            self._items[item.id] = item.model_copy(update={"cluster_id": target})
        return target

    def list_clusters(self) -> list[Cluster]:
        return sorted(self._clusters.values(), key=lambda c: c.id)

    # --- TL;DR -------------------------------------------------------------

    def tldr_candidates(self, as_of: datetime, pool_size: int) -> list[StoredItem]:
        """Stand-in for public.popular_feed('popular'): the same pool (published in the
        7 days before `as_of`, first seen by then, one row per cluster: its canonical
        item), but ordered by the cluster's best pop_pct, then newest, then id. The real
        ranking lives only in SQL; tests that need it run against Postgres."""
        if pool_size <= 0:
            return []
        pool = [
            i
            for i in self._items.values()
            if i.published_at > as_of - POPULAR_WINDOW and i.first_seen_at <= as_of
        ]
        heads: dict[str, list[StoredItem]] = defaultdict(list)
        for item in pool:
            cluster = self._clusters.get(item.cluster_id) if item.cluster_id else None
            heads[cluster.canonical_item_id if cluster else item.id].append(item)
        ranked = [
            (max(m.pop_pct or 0.0 for m in members), self._items[head])
            for head, members in heads.items()
            if any(m.id == head for m in members)  # canonical item outside the pool: no card
        ]
        ranked.sort(key=lambda r: r[1].id)
        ranked.sort(key=lambda r: (r[0], r[1].published_at), reverse=True)
        top = [item for _, item in ranked[:pool_size]]
        return [i for i in top if i.tldr is None and i.summarized_at is None]

    def save_tldrs(self, tldrs: Mapping[str, str], at: datetime) -> int:
        count = 0
        for item_id, tldr in tldrs.items():
            item = self._items.get(item_id)
            if item is not None:
                self._items[item_id] = item.model_copy(update={"tldr": tldr, "summarized_at": at})
                count += 1
        return count

    # --- user profiles and activity (W4) -------------------------------------

    def pending_profiles(self, limit: int) -> list[PendingProfile]:
        pending = sorted(
            (
                (p.updated_at, str(user_id), user_id, p)
                for user_id, p in self.profiles.items()
                if p.embedded_at is None and p.text is not None
            ),
        )[: max(limit, 0)]
        return [
            PendingProfile(
                user_id=user_id,
                text=p.text or "",
                updated_at=p.updated_at,
                centroid=self.follow_centroids.get(user_id),
            )
            for _, _, user_id, p in pending
        ]

    def save_profile_vector(
        self, profile: PendingProfile, vector: Sequence[float] | None, at: datetime
    ) -> bool:
        if vector is not None and len(vector) != EMBEDDING_DIM:
            raise StoreError(f"profile vector has {len(vector)} dims")
        row = self.profiles.get(profile.user_id)
        if (
            row is None
            or row.updated_at != profile.updated_at
            or row.embedded_at is not None
            or row.text is None
        ):
            return False
        row.user_vec = None if vector is None else [float(v) for v in np.float16(vector)]
        row.embedded_at = at
        row.updated_at = self._clock()  # profiles_touch
        return True

    def prune_activity(
        self, *, impressions_before: datetime, snapshots_before: datetime
    ) -> PruneResult:
        kept_impressions = [t for t in self.impressions if t >= impressions_before]
        kept_snapshots = [t for t in self.feed_snapshots if t >= snapshots_before]
        result = PruneResult(
            impressions=len(self.impressions) - len(kept_impressions),
            feed_snapshots=len(self.feed_snapshots) - len(kept_snapshots),
        )
        self.impressions, self.feed_snapshots = kept_impressions, kept_snapshots
        return result
