"""ContentStore backed by Supabase Postgres (psycopg 3).

The connection URL is passed in by the caller and never logged or stored on the object.
Every method runs in its own transaction (a savepoint if the caller already opened one).
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from types import TracebackType
from typing import Self

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from digest.store.base import DEFAULT_DUE_SLACK, EMBEDDING_DIM, StoreError
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

_SOURCE_COLUMNS = (
    "id, kind, name, url, category, enabled, base_weight, cadence_hours, etag, "
    "last_modified, last_fetched_at, last_ok_at, last_error, created_at"
)
# Everything except embedding (large) and fts (derived).
_ITEM_COLUMNS = (
    "id, cluster_id, source_id, title, url, discussion_url, description, image_url, lang, "
    "reading_min, published_at, fetched_at, native_score, native_comments, pop_pct, quality, "
    "tldr, native_tags, first_seen_at, enriched_at, tagged_at, embedded_at, summarized_at"
)

_SYNC_UPSERT = """
insert into public.sources (kind, name, url, category, base_weight, cadence_hours, enabled)
select u.kind, u.name, u.url, u.category, u.base_weight, u.cadence_hours, true
from unnest(%s::text[], %s::text[], %s::text[], %s::text[], %s::smallint[], %s::integer[])
  as u(kind, name, url, category, base_weight, cadence_hours)
on conflict (kind, url) do update set
  name = excluded.name,
  category = excluded.category,
  base_weight = excluded.base_weight,
  cadence_hours = excluded.cadence_hours,
  enabled = true
where (sources.name, sources.category, sources.base_weight, sources.cadence_hours, sources.enabled)
  is distinct from
  (excluded.name, excluded.category, excluded.base_weight, excluded.cadence_hours, true)
returning (xmax = 0) as inserted
"""

_SYNC_DISABLE = """
update public.sources s set enabled = false
where s.enabled
  and not exists (
    select 1 from unnest(%s::text[], %s::text[]) as w(kind, url)
    where w.kind = s.kind and w.url = s.url
  )
"""

# Same source: refresh popularity. Another source: the row keeps the first source's
# numbers (pop_pct is per source). Either way new native tags are merged in, and an item
# whose tags grew is put back on the tag queue. Rows with nothing to change are skipped.
_UPSERT_ITEMS = """
insert into public.items (
  id, source_id, title, url, discussion_url, description, published_at, fetched_at,
  native_score, native_comments, native_tags, first_seen_at
)
select u.id, u.source_id, u.title, u.url, u.discussion_url, u.description, u.published_at,
       u.fetched_at, u.native_score, u.native_comments,
       coalesce(array(select jsonb_array_elements_text(u.native_tags)), '{}'), u.fetched_at
from unnest(
  %s::text[], %s::bigint[], %s::text[], %s::text[], %s::text[], %s::text[],
  %s::timestamptz[], %s::timestamptz[], %s::integer[], %s::integer[], %s::jsonb[]
) as u(id, source_id, title, url, discussion_url, description, published_at, fetched_at,
       native_score, native_comments, native_tags)
on conflict (id) do update set
  native_score = case when items.source_id = excluded.source_id
                      then excluded.native_score else items.native_score end,
  native_comments = case when items.source_id = excluded.source_id
                         then excluded.native_comments else items.native_comments end,
  fetched_at = case when items.source_id = excluded.source_id
                    then excluded.fetched_at else items.fetched_at end,
  native_tags = items.native_tags
    || array(select t from unnest(excluded.native_tags) t where t <> all(items.native_tags)),
  tagged_at = case when excluded.native_tags <@ items.native_tags
                   then items.tagged_at end
where items.source_id = excluded.source_id
   or not (excluded.native_tags <@ items.native_tags)
returning id, (xmax = 0) as inserted, source_id
"""

_UPSERT_MENTIONS = """
insert into public.item_mentions (
  item_id, source_id, discussion_url, native_score, native_comments, first_seen_at
)
select * from unnest(
  %s::text[], %s::bigint[], %s::text[], %s::integer[], %s::integer[], %s::timestamptz[]
)
on conflict (item_id, source_id) do update set
  discussion_url = coalesce(excluded.discussion_url, item_mentions.discussion_url),
  native_score = excluded.native_score,
  native_comments = excluded.native_comments
"""

# Description is only filled when missing; the other fields keep their value on null.
_SAVE_ENRICHMENT = """
update public.items i set
  image_url = coalesce(u.image_url, i.image_url),
  description = coalesce(i.description, u.description),
  reading_min = coalesce(u.reading_min, i.reading_min),
  lang = coalesce(u.lang, i.lang),
  enriched_at = %s
from unnest(%s::text[], %s::text[], %s::text[], %s::smallint[], %s::text[])
  as u(id, image_url, description, reading_min, lang)
where i.id = u.id
"""

# Mid-rank percentile of native_score per source: (#lower + #lower-or-equal - 1) / 2(n - 1).
# rank() - 1 counts lower scores; count(*) over an ordered window (default RANGE frame)
# counts lower-or-equal ones. A lone item, or a source whose items all tie, gets 0.5.
_RECOMPUTE_POP_PCT = """
with ranked as (
  select id,
         (case when count(*) over src = 1 then 0.5
               else ((rank() over ordered - 1) + (count(*) over ordered - 1))::float8
                    / (2 * (count(*) over src - 1))
          end)::real as pct
  from public.items
  where published_at >= %s
  window src as (partition by source_id),
         ordered as (partition by source_id order by native_score)
)
update public.items i set pop_pct = r.pct
from ranked r
where i.id = r.id and i.pop_pct is distinct from r.pct
"""

_SYNC_TAGS = """
insert into public.tags (slug, name, parent, description, aliases, active)
select u.slug, u.name, u.parent, u.description,
       coalesce(array(select jsonb_array_elements_text(u.aliases)), '{}'), true
from unnest(%s::text[], %s::text[], %s::text[], %s::text[], %s::jsonb[])
  as u(slug, name, parent, description, aliases)
on conflict (slug) do update set
  name = excluded.name,
  parent = excluded.parent,
  description = excluded.description,
  aliases = excluded.aliases,
  active = true
where (tags.name, tags.parent, tags.description, tags.aliases, tags.active)
  is distinct from
  (excluded.name, excluded.parent, excluded.description, excluded.aliases, true)
returning (xmax = 0) as inserted
"""

_DELETE_ITEM_TAGS = """
delete from public.item_tags t
using unnest(%s::text[], %s::text[]) as d(item_id, method)
where t.item_id = d.item_id and t.method = d.method
"""

# A tag still present from another method keeps whichever confidence is higher.
_INSERT_ITEM_TAGS = """
insert into public.item_tags (item_id, tag, confidence, method)
select * from unnest(%s::text[], %s::text[], %s::real[], %s::text[])
on conflict (item_id, tag) do update set
  confidence = excluded.confidence,
  method = excluded.method
where excluded.confidence > item_tags.confidence
"""

_SAVE_TAG_STATE = """
update public.items i set
  quality = coalesce(u.quality, i.quality),
  tagged_at = case when u.done then %s else i.tagged_at end
from unnest(%s::text[], %s::real[], %s::boolean[]) as u(id, quality, done)
where i.id = u.id
"""

_SAVE_EMBEDDINGS = """
update public.items i set embedding = u.vec::extensions.halfvec, embedded_at = %s
from unnest(%s::text[], %s::text[]) as u(id, vec)
where i.id = u.id
"""

# Collect the given items plus every member of their existing clusters; keep the lowest
# cluster id (or create one), point every member at it, delete the merged-away clusters.
_MERGE_CLUSTER = """
with given as (
  select id, cluster_id from public.items where id = any(%(ids)s::text[])
),
old as (
  select distinct cluster_id as id from given where cluster_id is not null
),
members as (
  select id from given
  union
  select unnest(c.item_ids) from public.clusters c join old on old.id = c.id
),
ordered as (
  select i.id from public.items i join members m on m.id = i.id
  order by i.first_seen_at, i.id
),
agg as (
  select array_agg(id) as ids, (array_agg(id))[1] as canonical,
         (select min(id) from old) as keep
  from ordered
  having count(*) >= 2
),
created as (
  insert into public.clusters (canonical_item_id, item_ids)
  select canonical, ids from agg where keep is null
  returning id
),
target as (
  select coalesce((select keep from agg), (select id from created)) as id
),
kept as (
  update public.clusters c set canonical_item_id = agg.canonical, item_ids = agg.ids
  from agg where c.id = agg.keep
  returning c.id
),
repointed as (
  update public.items i set cluster_id = target.id
  from target, agg where i.id = any(agg.ids) and i.cluster_id is distinct from target.id
  returning i.id
),
dropped as (
  delete from public.clusters c using old, target
  where c.id = old.id and c.id <> target.id
  returning c.id
)
select (select id from target)
"""


class PostgresStore:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    @classmethod
    def connect(cls, url: str) -> Self:
        # prepare_threshold=None: no server-side prepared statements, so the store also
        # works behind Supavisor in transaction mode.
        return cls(psycopg.connect(url, autocommit=True, prepare_threshold=None))

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- sources -----------------------------------------------------------

    def sync_sources(self, specs: Sequence[SourceSpec]) -> SyncResult:
        wanted = list({spec.key: spec for spec in specs}.values())
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                _SYNC_UPSERT,
                (
                    [s.kind for s in wanted],
                    [s.name for s in wanted],
                    [s.url for s in wanted],
                    [s.category for s in wanted],
                    [s.base_weight for s in wanted],
                    [s.cadence_hours for s in wanted],
                ),
            )
            flags = [bool(row[0]) for row in cur.fetchall()]
            cur.execute(_SYNC_DISABLE, ([s.kind for s in wanted], [s.url for s in wanted]))
            disabled = cur.rowcount
        inserted = sum(flags)
        return SyncResult(inserted=inserted, updated=len(flags) - inserted, disabled=disabled)

    def list_sources(self, *, enabled_only: bool = False) -> list[Source]:
        query = f"select {_SOURCE_COLUMNS} from public.sources"
        if enabled_only:
            query += " where enabled"
        return self._sources(query + " order by id", ())

    def due_sources(self, now: datetime, *, slack: timedelta = DEFAULT_DUE_SLACK) -> list[Source]:
        query = f"""
            select {_SOURCE_COLUMNS} from public.sources
            where enabled
              and (last_fetched_at is null
                   or last_fetched_at + make_interval(hours => cadence_hours) <= %s)
            order by id
        """
        return self._sources(query, (now + slack,))

    def record_fetch(self, source_id: int, outcome: FetchOutcome) -> None:
        if outcome.ok:
            query = """
                update public.sources set
                  last_fetched_at = %(at)s, last_ok_at = %(at)s, last_error = null,
                  etag = coalesce(%(etag)s, etag),
                  last_modified = coalesce(%(last_modified)s, last_modified)
                where id = %(id)s
            """
        else:
            query = """
                update public.sources set last_fetched_at = %(at)s, last_error = %(error)s
                where id = %(id)s
            """
        params = outcome.model_dump(include={"at", "etag", "last_modified", "error"})
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(query, {**params, "id": source_id})
            if cur.rowcount == 0:
                raise StoreError(f"unknown source id {source_id}")

    # --- items -------------------------------------------------------------

    def upsert_items(self, items: Sequence[NewItem]) -> UpsertResult:
        batch = list({item.id: item for item in items}.values())
        if not batch:
            return UpsertResult()
        columns = (
            "id",
            "source_id",
            "title",
            "url",
            "discussion_url",
            "description",
            "published_at",
            "fetched_at",
            "native_score",
            "native_comments",
        )
        params = (
            *([getattr(item, c) for item in batch] for c in columns),
            [Jsonb(item.native_tags) for item in batch],
        )
        mention_columns = (
            "id",
            "source_id",
            "discussion_url",
            "native_score",
            "native_comments",
            "fetched_at",
        )
        mentions = tuple([getattr(item, c) for item in batch] for c in mention_columns)
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute(_UPSERT_ITEMS, params)
                rows = cur.fetchall()
                cur.execute(_UPSERT_MENTIONS, mentions)
        except psycopg.errors.ForeignKeyViolation as exc:
            raise StoreError("item references an unknown source id") from exc
        source_of = {item.id: item.source_id for item in batch}
        inserted = {row[0] for row in rows if row[1]}
        new_ids = [item.id for item in batch if item.id in inserted]
        refreshed = sum(1 for row in rows if not row[1] and row[2] == source_of[row[0]])
        return UpsertResult(
            new_ids=new_ids,
            refreshed=refreshed,
            other_source=len(batch) - len(new_ids) - refreshed,
        )

    def get_mentions(self, ids: Sequence[str]) -> list[Mention]:
        query = """
            select item_id, source_id, discussion_url, native_score, native_comments,
                   first_seen_at
            from public.item_mentions where item_id = any(%s::text[])
            order by item_id, first_seen_at, source_id
        """
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, (list(dict.fromkeys(ids)),))
            return [Mention.model_validate(row) for row in cur.fetchall()]

    def get_items(self, ids: Sequence[str]) -> list[StoredItem]:
        wanted = list(dict.fromkeys(ids))
        query = f"select {_ITEM_COLUMNS} from public.items where id = any(%s::text[])"
        found = {item.id: item for item in self._items(query, (wanted,))}
        return [found[i] for i in wanted if i in found]

    def pending(self, stage: Stage, limit: int, *, after: Stage | None = None) -> list[StoredItem]:
        done_before = (
            sql.SQL(" and {} is not null").format(sql.Identifier(after.column))
            if after is not None
            else sql.SQL("")
        )
        query = sql.SQL(
            "select {cols} from public.items where {col} is null{after} "
            "order by first_seen_at desc, id limit %s"
        ).format(cols=sql.SQL(_ITEM_COLUMNS), col=sql.Identifier(stage.column), after=done_before)
        return self._items(query, (max(limit, 0),))

    def mark_done(self, stage: Stage, ids: Sequence[str], at: datetime) -> int:
        query = sql.SQL("update public.items set {col} = %s where id = any(%s::text[])").format(
            col=sql.Identifier(stage.column)
        )
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(query, (at, list(dict.fromkeys(ids))))
            return cur.rowcount

    def save_enrichment(self, updates: Sequence[EnrichmentUpdate], at: datetime) -> int:
        batch = list({u.id: u for u in updates}.values())
        if not batch:
            return 0
        columns = ("id", "image_url", "description", "reading_min", "lang")
        params = (at, *([getattr(u, c) for u in batch] for c in columns))
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(_SAVE_ENRICHMENT, params)
            return cur.rowcount

    def recompute_pop_pct(self, now: datetime, window: timedelta) -> int:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(_RECOMPUTE_POP_PCT, (now - window,))
            return cur.rowcount

    # --- tags --------------------------------------------------------------

    def sync_tags(self, specs: Sequence[TagSpec]) -> SyncResult:
        wanted = list({spec.slug: spec for spec in specs}.values())
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute(
                    _SYNC_TAGS,
                    (
                        [t.slug for t in wanted],
                        [t.name for t in wanted],
                        [t.parent for t in wanted],
                        [t.description for t in wanted],
                        [Jsonb(t.aliases) for t in wanted],
                    ),
                )
                flags = [bool(row[0]) for row in cur.fetchall()]
                cur.execute(
                    "update public.tags set active = false "
                    "where active and slug <> all(%s::text[])",
                    ([t.slug for t in wanted],),
                )
                disabled = cur.rowcount
        except psycopg.errors.ForeignKeyViolation as exc:
            raise StoreError("tag references an unknown parent") from exc
        inserted = sum(flags)
        return SyncResult(inserted=inserted, updated=len(flags) - inserted, disabled=disabled)

    def list_tags(self, *, active_only: bool = False) -> list[Tag]:
        query = "select slug, name, parent, description, aliases, active from public.tags"
        if active_only:
            query += " where active"
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query + " order by slug")
            return [Tag.model_validate(row) for row in cur.fetchall()]

    def save_tags(self, updates: Sequence[TagUpdate], at: datetime) -> int:
        batch = list({u.id: u for u in updates}.values())
        if not batch:
            return 0
        drop = [(u.id, str(m)) for u in batch for m in sorted(u.methods)]
        add = [(u.id, t.tag, t.confidence, str(t.method)) for u in batch for t in u.tags]
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute(_DELETE_ITEM_TAGS, ([d[0] for d in drop], [d[1] for d in drop]))
                if add:
                    cur.execute(
                        _INSERT_ITEM_TAGS, tuple(list(col) for col in zip(*add, strict=True))
                    )
                cur.execute(
                    _SAVE_TAG_STATE,
                    (
                        at,
                        [u.id for u in batch],
                        [u.quality for u in batch],
                        [u.done for u in batch],
                    ),
                )
                updated = cur.rowcount
        except psycopg.errors.ForeignKeyViolation as exc:
            raise StoreError("item tag references an unknown item or tag") from exc
        if updated != len(batch):
            raise StoreError("save_tags: unknown item id")
        return updated

    def get_item_tags(self, ids: Sequence[str]) -> list[StoredItemTag]:
        query = """
            select item_id, tag, confidence, method from public.item_tags
            where item_id = any(%s::text[])
            order by item_id, confidence desc, tag
        """
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, (list(dict.fromkeys(ids)),))
            return [StoredItemTag.model_validate(row) for row in cur.fetchall()]

    # --- embeddings --------------------------------------------------------

    def save_embeddings(self, vectors: Mapping[str, Sequence[float]], at: datetime) -> int:
        if not vectors:
            return 0
        for item_id, vector in vectors.items():
            if len(vector) != EMBEDDING_DIM:
                raise StoreError(f"embedding of {item_id} has {len(vector)} dims")
        texts = ["[" + ",".join(f"{float(x):.6g}" for x in v) + "]" for v in vectors.values()]
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(_SAVE_EMBEDDINGS, (at, list(vectors), texts))
            return cur.rowcount

    def get_embeddings(self, ids: Sequence[str]) -> dict[str, list[float]]:
        query = """
            select id, embedding::extensions.vector::real[] from public.items
            where id = any(%s::text[]) and embedding is not null
        """
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(query, (list(dict.fromkeys(ids)),))
            return {row[0]: row[1] for row in cur.fetchall()}

    # --- clusters ----------------------------------------------------------

    def recent_items(self, since: datetime) -> list[StoredItem]:
        query = f"""
            select {_ITEM_COLUMNS} from public.items
            where published_at >= %s and embedded_at is not null
            order by published_at, id
        """
        return self._items(query, (since,))

    def merge_cluster(self, item_ids: Sequence[str]) -> int:
        ids = list(dict.fromkeys(item_ids))
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(_MERGE_CLUSTER, {"ids": ids})
            row = cur.fetchone()
        if row is None or row[0] is None:
            raise StoreError("a cluster needs at least two known items")
        return int(row[0])

    def list_clusters(self) -> list[Cluster]:
        query = "select id, canonical_item_id, item_ids from public.clusters order by id"
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query)
            return [Cluster.model_validate(row) for row in cur.fetchall()]

    # --- helpers -----------------------------------------------------------

    def _sources(self, query: str, params: tuple) -> list[Source]:
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return [Source.model_validate(row) for row in cur.fetchall()]

    def _items(self, query: str | sql.Composed, params: tuple) -> list[StoredItem]:
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return [StoredItem.model_validate(row) for row in cur.fetchall()]
