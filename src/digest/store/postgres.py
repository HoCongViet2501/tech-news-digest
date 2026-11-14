"""ContentStore backed by Supabase Postgres (psycopg 3).

The connection URL is passed in by the caller and never logged or stored on the object.
Every method runs in its own transaction (a savepoint if the caller already opened one).
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from types import TracebackType
from typing import Self

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

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

_SOURCE_COLUMNS = (
    "id, kind, name, url, category, enabled, base_weight, cadence_hours, etag, "
    "last_modified, last_fetched_at, last_ok_at, last_error"
)
# Everything except embedding (large) and fts (derived).
_ITEM_COLUMNS = (
    "id, cluster_id, source_id, title, url, discussion_url, description, image_url, lang, "
    "reading_min, published_at, fetched_at, native_score, native_comments, pop_pct, quality, "
    "tldr, first_seen_at, enriched_at, tagged_at, embedded_at, summarized_at"
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

_UPSERT_ITEMS = """
insert into public.items (
  id, source_id, title, url, discussion_url, description, published_at, fetched_at,
  native_score, native_comments, first_seen_at
)
select u.id, u.source_id, u.title, u.url, u.discussion_url, u.description, u.published_at,
       u.fetched_at, u.native_score, u.native_comments, u.fetched_at
from unnest(
  %s::text[], %s::bigint[], %s::text[], %s::text[], %s::text[], %s::text[],
  %s::timestamptz[], %s::timestamptz[], %s::integer[], %s::integer[]
) as u(id, source_id, title, url, discussion_url, description, published_at, fetched_at,
       native_score, native_comments)
on conflict (id) do update set
  native_score = excluded.native_score,
  native_comments = excluded.native_comments,
  fetched_at = excluded.fetched_at
returning id, (xmax = 0) as inserted
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
        params = tuple([getattr(item, c) for item in batch] for c in columns)
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute(_UPSERT_ITEMS, params)
                inserted = {row[0] for row in cur.fetchall() if row[1]}
        except psycopg.errors.ForeignKeyViolation as exc:
            raise StoreError("item references an unknown source id") from exc
        new_ids = [item.id for item in batch if item.id in inserted]
        return UpsertResult(new_ids=new_ids, refreshed=len(batch) - len(new_ids))

    def get_items(self, ids: Sequence[str]) -> list[StoredItem]:
        wanted = list(dict.fromkeys(ids))
        query = f"select {_ITEM_COLUMNS} from public.items where id = any(%s::text[])"
        found = {item.id: item for item in self._items(query, (wanted,))}
        return [found[i] for i in wanted if i in found]

    def pending(self, stage: Stage, limit: int) -> list[StoredItem]:
        query = sql.SQL(
            "select {cols} from public.items where {col} is null "
            "order by first_seen_at desc, id limit %s"
        ).format(cols=sql.SQL(_ITEM_COLUMNS), col=sql.Identifier(stage.column))
        return self._items(query, (max(limit, 0),))

    def mark_done(self, stage: Stage, ids: Sequence[str], at: datetime) -> int:
        query = sql.SQL("update public.items set {col} = %s where id = any(%s::text[])").format(
            col=sql.Identifier(stage.column)
        )
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(query, (at, list(dict.fromkeys(ids))))
            return cur.rowcount

    # --- helpers -----------------------------------------------------------

    def _sources(self, query: str, params: tuple) -> list[Source]:
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return [Source.model_validate(row) for row in cur.fetchall()]

    def _items(self, query: str | sql.Composed, params: tuple) -> list[StoredItem]:
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return [StoredItem.model_validate(row) for row in cur.fetchall()]
