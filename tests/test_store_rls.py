"""Schema and security checks against the migrated Postgres (local Supabase).

anon/authenticated may only SELECT public content; the pipeline writes as postgres.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg import sql
from psycopg.errors import CheckViolation, InsufficientPrivilege

from digest.store import NewItem, PostgresStore, SourceSpec

pytestmark = pytest.mark.integration

CONTENT_TABLES = ("sources", "items", "clusters", "tags", "item_tags")
CLIENT_ROLES = ("anon", "authenticated")
T0 = datetime(2026, 11, 15, 7, 0, tzinfo=UTC)
ITEM_ID = "a" * 40


@contextmanager
def as_role(conn: psycopg.Connection, role: str) -> Iterator[None]:
    """Run statements as `role` inside a savepoint that is always rolled back."""
    with conn.transaction(force_rollback=True):
        conn.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
        yield


@pytest.fixture
def seeded(pg_conn: psycopg.Connection) -> psycopg.Connection:
    store = PostgresStore(pg_conn)
    store.sync_sources([SourceSpec(kind="rss", name="Blog", url="https://blog.example/feed")])
    source_id = store.list_sources()[0].id
    store.upsert_items(
        [
            NewItem(
                id=ITEM_ID,
                source_id=source_id,
                title="Kiến trúc microservices với Kafka",
                url="https://blog.example/kien-truc",
                description="Thiết kế hệ thống",
                published_at=T0,
                fetched_at=T0,
            )
        ]
    )
    pg_conn.execute("insert into public.tags (slug, name) values ('kafka', 'Kafka')")
    pg_conn.execute(
        "insert into public.item_tags (item_id, tag, confidence, method) "
        "values (%s, 'kafka', 0.95, 'native')",
        (ITEM_ID,),
    )
    return pg_conn


@pytest.mark.parametrize("role", CLIENT_ROLES)
def test_clients_can_read_public_content(seeded: psycopg.Connection, role: str) -> None:
    with as_role(seeded, role):
        # sources only exposes some columns (see the next test), so no `select *` there.
        for table in CONTENT_TABLES[1:]:
            seeded.execute(sql.SQL("select * from public.{}").format(sql.Identifier(table)))
        assert seeded.execute("select count(*) from public.items").fetchone() == (1,)
        assert seeded.execute("select count(*) from public.item_tags").fetchone() == (1,)
        assert seeded.execute("select name, url, enabled from public.sources").fetchall() == [
            ("Blog", "https://blog.example/feed", True)
        ]


@pytest.mark.parametrize("role", CLIENT_ROLES)
@pytest.mark.parametrize("column", ["etag", "last_modified", "last_error", "last_fetched_at"])
def test_clients_cannot_read_source_fetch_state(
    seeded: psycopg.Connection, role: str, column: str
) -> None:
    query = sql.SQL("select {} from public.sources").format(sql.Identifier(column))
    with pytest.raises(InsufficientPrivilege), as_role(seeded, role):
        seeded.execute(query)


WRITES = {
    "insert": "insert into public.items (id, source_id, title, url, published_at) "
    "select %s, source_id, 'x', 'https://x', now() from public.items limit 1",
    "update": "update public.items set title = 'pwned' where id = %s",
    "delete": "delete from public.items where id = %s",
}


@pytest.mark.parametrize("role", CLIENT_ROLES)
@pytest.mark.parametrize("kind", sorted(WRITES))
def test_clients_cannot_write_items(seeded: psycopg.Connection, role: str, kind: str) -> None:
    item_id = "b" * 40 if kind == "insert" else ITEM_ID
    with pytest.raises(InsufficientPrivilege), as_role(seeded, role):
        seeded.execute(WRITES[kind], (item_id,))
    assert seeded.execute("select title from public.items").fetchall() == [
        ("Kiến trúc microservices với Kafka",)
    ]


@pytest.mark.parametrize("role", CLIENT_ROLES)
@pytest.mark.parametrize("table", CONTENT_TABLES)
def test_clients_cannot_truncate(seeded: psycopg.Connection, role: str, table: str) -> None:
    with pytest.raises(InsufficientPrivilege), as_role(seeded, role):
        seeded.execute(sql.SQL("truncate public.{} cascade").format(sql.Identifier(table)))


def test_client_grants_are_select_only(pg_conn: psycopg.Connection) -> None:
    table_grants = pg_conn.execute(
        """
        select grantee, table_name, privilege_type
        from information_schema.role_table_grants
        where table_schema = 'public' and grantee = any(%s) and privilege_type <> 'SELECT'
        """,
        (list(CLIENT_ROLES),),
    ).fetchall()
    column_grants = pg_conn.execute(
        """
        select grantee, table_name, column_name, privilege_type
        from information_schema.column_privileges
        where table_schema = 'public' and grantee = any(%s) and privilege_type <> 'SELECT'
        """,
        (list(CLIENT_ROLES),),
    ).fetchall()
    sequences = pg_conn.execute(
        """
        select c.relname, r.role
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        cross join unnest(%s::text[]) as r(role)
        where n.nspname = 'public' and c.relkind = 'S'
          and has_sequence_privilege(r.role, c.oid, 'USAGE,UPDATE')
        """,
        (list(CLIENT_ROLES),),
    ).fetchall()

    assert table_grants == []
    assert column_grants == []
    assert sequences == []


def test_rls_enabled_and_policies_are_select_only(pg_conn: psycopg.Connection) -> None:
    no_rls = pg_conn.execute(
        """
        select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity
        """
    ).fetchall()
    policies = pg_conn.execute(
        "select tablename, cmd from pg_policies where schemaname = 'public' order by tablename"
    ).fetchall()

    assert no_rls == []
    assert sorted(policies) == sorted((t, "SELECT") for t in CONTENT_TABLES)


# --- schema behaviour ------------------------------------------------------


def test_fts_matches_without_diacritics(seeded: psycopg.Connection) -> None:
    query = (
        "select id from public.items "
        "where fts @@ websearch_to_tsquery('simple', public.immutable_unaccent(%s))"
    )
    assert seeded.execute(query, ("kien truc",)).fetchall() == [(ITEM_ID,)]
    assert seeded.execute(query, ("thiết kế",)).fetchall() == [(ITEM_ID,)]
    assert seeded.execute(query, ("postgres",)).fetchall() == []


def test_title_trigram_similarity(seeded: psycopg.Connection) -> None:
    row = seeded.execute(
        "select id from public.items where title operator(extensions.%%) %s", ("microservises",)
    ).fetchone()
    assert row == (ITEM_ID,)


def test_embedding_is_halfvec_384(seeded: psycopg.Connection) -> None:
    vec = "[" + ",".join(["0.5"] * 384) + "]"
    seeded.execute(
        "update public.items set embedding = %s::extensions.halfvec where id = %s", (vec, ITEM_ID)
    )
    with pytest.raises(psycopg.errors.DataException), seeded.transaction():
        seeded.execute(
            "update public.items set embedding = '[1,2,3]'::extensions.halfvec where id = %s",
            (ITEM_ID,),
        )


@pytest.mark.parametrize(
    ("confidence", "method"), [(1.5, "native"), (-0.1, "rule"), (0.5, "guess")]
)
def test_item_tags_checks(seeded: psycopg.Connection, confidence: float, method: str) -> None:
    seeded.execute("insert into public.tags (slug, name) values ('go', 'Go')")
    with pytest.raises(CheckViolation), seeded.transaction():
        seeded.execute(
            "insert into public.item_tags (item_id, tag, confidence, method) "
            "values (%s, 'go', %s, %s)",
            (ITEM_ID, confidence, method),
        )


def test_deleting_item_cascades_to_tags(seeded: psycopg.Connection) -> None:
    seeded.execute("delete from public.items where id = %s", (ITEM_ID,))
    assert seeded.execute("select count(*) from public.item_tags").fetchone() == (0,)
