"""W4 accounts and personalization, against the migrated Postgres (local Supabase).

Clients are simulated the way PostgREST runs them: `set local role authenticated` plus
`request.jwt.claims` holding the user id, so auth.uid() and RLS behave as in production.
Test users are inserted into auth.users inside the rolled-back test transaction.
"""

import json
import math
import random
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.errors import (
    CheckViolation,
    InsufficientPrivilege,
    InvalidParameterValue,
    ProgramLimitExceeded,
)
from psycopg.pq import TransactionStatus

from digest.config import RankingConfig
from digest.store import NewItem, PostgresStore, SourceSpec, TagSpec

pytestmark = pytest.mark.integration

PAGE = 24
DIM = 384
# Tag tree: backend > messaging > kafka, backend > databases; frontend > react; ai.
TAGS = [
    TagSpec(slug="backend", name="Backend"),
    TagSpec(slug="messaging", name="Messaging", parent="backend"),
    TagSpec(slug="kafka", name="Kafka", parent="messaging"),
    TagSpec(slug="databases", name="Databases", parent="backend"),
    TagSpec(slug="frontend", name="Frontend"),
    TagSpec(slug="react", name="React", parent="frontend"),
    TagSpec(slug="css", name="CSS", parent="frontend"),
    TagSpec(slug="ai", name="AI"),
    TagSpec(slug="go", name="Go"),
    TagSpec(slug="rust", name="Rust"),
]
BACKEND_FAMILY = {"backend", "messaging", "kafka", "databases"}
# Each topic gets its own block of embedding dimensions, so items of a topic are close.
TOPICS = {"kafka": 0, "databases": 1, "react": 2, "css": 3, "ai": 4}


@dataclass(frozen=True)
class World:
    conn: psycopg.Connection
    items: dict[str, str]  # item id -> its single tag
    sources: dict[str, int]  # item id -> source id


def _vector(topic: str, rng: random.Random) -> list[float]:
    base = TOPICS[topic] * 40
    vec = [rng.gauss(0, 0.05) for _ in range(DIM)]
    for d in range(base, base + 40):
        vec[d] += 1.0
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


@pytest.fixture
def world(pg_conn: psycopg.Connection) -> World:
    """200 items over 10 sources, 40 per topic, published in the last 3 days, all with
    the same popularity and quality, so interest decides the order."""
    rng = random.Random(7)
    store = PostgresStore(pg_conn)
    store.sync_ranking(RankingConfig())
    store.sync_tags(TAGS)
    store.sync_sources(
        [
            SourceSpec(kind="rss", name=f"Blog {n}", url=f"https://b{n}.example/feed")
            for n in range(10)
        ]
    )
    source_ids = [s.id for s in store.list_sources()]
    # The pool needs first_seen_at (= fetched_at) <= now(), the transaction start.
    now = datetime.now(UTC) - timedelta(minutes=5)
    items: dict[str, str] = {}
    sources: dict[str, int] = {}
    new: list[NewItem] = []
    for n in range(200):
        topic = list(TOPICS)[n % len(TOPICS)]
        item_id = f"{n + 1:040x}"
        items[item_id] = topic
        sources[item_id] = source_ids[n % len(source_ids)]
        new.append(
            NewItem(
                id=item_id,
                source_id=sources[item_id],
                title=f"{topic} story {n}",
                url=f"https://example.com/{n}",
                published_at=now - timedelta(hours=1 + (n * 7) % 72),
                fetched_at=now,
            )
        )
    store.upsert_items(new)
    store.save_embeddings({i: _vector(t, rng) for i, t in items.items()}, now)
    pg_conn.execute(
        "insert into public.item_tags (item_id, tag, confidence, method) "
        "select u.id, u.tag, 0.95, 'rule' from unnest(%s::text[], %s::text[]) as u(id, tag)",
        (list(items), list(items.values())),
    )
    pg_conn.execute("update public.items set pop_pct = 0.5, quality = 0.5")
    return World(pg_conn, items, sources)


def make_user(conn: psycopg.Connection, email: str, meta: dict[str, Any] | None = None) -> str:
    user_id = str(uuid.uuid4())
    conn.execute(
        "insert into auth.users (id, email, aud, role, raw_user_meta_data) "
        "values (%s, %s, 'authenticated', 'authenticated', %s)",
        (user_id, email, json.dumps(meta or {})),
    )
    return user_id


@contextmanager
def as_role(
    conn: psycopg.Connection, role: str, user_id: str | None = None
) -> Iterator[psycopg.Connection]:
    """Run as a Data API role. Changes persist in the test transaction (unlike a
    rolled-back savepoint), so several calls can build on each other."""
    claims = {"role": role} | ({"sub": user_id} if user_id else {})
    conn.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
    conn.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))
    try:
        yield conn
    finally:
        if conn.info.transaction_status != TransactionStatus.INERROR:
            conn.execute("reset role")
            conn.execute("select set_config('request.jwt.claims', '', true)")


def as_user(conn: psycopg.Connection, user_id: str) -> Any:
    return as_role(conn, "authenticated", user_id)


def follow(
    conn: psycopg.Connection, user_id: str, tags: list[str], text: str | None = None
) -> None:
    with as_user(conn, user_id):
        conn.execute("select public.set_followed_tags(%s, %s)", (tags, text))


def page(
    conn: psycopg.Connection,
    user_id: str,
    snapshot_id: int | None = None,
    after: int = 0,
    *,
    refresh: bool = False,
) -> list[tuple[int, int, str]]:
    """(snapshot_id, pos, item id) rows of one My Feed page."""
    with as_user(conn, user_id):
        return conn.execute(
            "select snapshot_id, pos, (card).id from public.my_feed(%s, %s, null, %s)",
            (snapshot_id, after, refresh),
        ).fetchall()


def next_page(conn: psycopg.Connection, user_id: str, rows: list[tuple[int, int, str]]) -> Any:
    return page(conn, user_id, rows[-1][0], rows[-1][1])


def call(conn: psycopg.Connection, user_id: str, query: str, params: tuple = ()) -> Any:
    with as_user(conn, user_id):
        return conn.execute(query, params).fetchall()


# --- ranking ---------------------------------------------------------------


def test_users_with_different_tags_get_different_feeds(world: World) -> None:
    conn = world.conn
    backend = make_user(conn, "be@example.com")
    frontend = make_user(conn, "fe@example.com")
    follow(conn, backend, ["backend", "go", "rust"])
    follow(conn, frontend, ["frontend", "go", "rust"])

    top_be = [world.items[r[2]] for r in page(conn, backend)[:7]]  # slot 8 is explore
    top_fe = [world.items[r[2]] for r in page(conn, frontend)[:7]]

    # `backend` covers its descendants: the items are tagged kafka / databases only.
    assert set(top_be) <= {"kafka", "databases"}
    assert set(top_fe) <= {"react", "css"}
    # MMR mixes both followed subtopics instead of one near-duplicate run.
    assert set(top_be) == {"kafka", "databases"}
    assert set(top_fe) == {"react", "css"}


def test_cold_start_centroid_points_at_the_followed_tags(world: World) -> None:
    conn = world.conn
    user = make_user(conn, "cold@example.com")
    follow(conn, user, ["kafka", "go", "rust"])

    nearest = conn.execute(
        "select i.id from public.items i "
        "order by extensions.cosine_distance(i.embedding, public.follow_centroid(%s)) limit 10",
        (user,),
    ).fetchall()

    assert {world.items[r[0]] for r in nearest} == {"kafka"}
    assert conn.execute("select public.follow_centroid(%s)", (str(uuid.uuid4()),)).fetchone() == (
        None,
    )


def test_user_vec_replaces_the_centroid(world: World) -> None:
    conn = world.conn
    user = make_user(conn, "vec@example.com")
    follow(conn, user, ["go", "rust", "backend"])  # tag_match: backend items
    ai_vec = "[" + ",".join(f"{x:.6g}" for x in _vector("ai", random.Random(1))) + "]"
    conn.execute(
        "update public.profiles set user_vec = %s::extensions.halfvec where user_id = %s",
        (ai_vec, user),
    )
    conn.execute("update public.ranking_config set interest_tag_weight = 0")  # cos only

    top = [world.items[r[2]] for r in page(conn, user)[:3]]

    assert top == ["ai", "ai", "ai"]


def test_five_pages_have_no_duplicates(world: World) -> None:
    conn = world.conn
    user = make_user(conn, "pages@example.com")
    follow(conn, user, ["backend", "ai", "react"])

    rows = page(conn, user)
    seen = list(rows)
    for _ in range(4):
        rows = next_page(conn, user, rows)
        seen += rows

    ids = [r[2] for r in seen]
    assert len(ids) == 5 * PAGE
    assert len(set(ids)) == len(ids)
    assert len({r[0] for r in seen}) == 1  # one snapshot
    assert [r[1] for r in seen] == list(range(1, 5 * PAGE + 1))


def test_at_most_three_items_per_source_per_page(world: World) -> None:
    conn = world.conn
    user = make_user(conn, "cap@example.com")
    follow(conn, user, ["backend", "go", "rust"])

    rows = page(conn, user)
    for _ in range(2):
        counts = Counter(world.sources[r[2]] for r in rows)
        assert max(counts.values()) <= 3
        rows = next_page(conn, user, rows)


def test_explore_slots_come_from_popular(world: World) -> None:
    conn = world.conn
    conn.execute("update public.ranking_config set candidates = 20, explore_ratio = 0.125")
    # The most popular items are frontend ones the user does not follow.
    popular = [i for i, t in world.items.items() if t == "css"][:5]
    conn.execute("update public.items set pop_pct = 1, quality = 1 where id = any(%s)", (popular,))
    user = make_user(conn, "explore@example.com")
    follow(conn, user, ["kafka", "go", "rust"])

    rows = page(conn, user)

    tags = [world.items[r[2]] for r in rows]
    assert tags[7] == "css" and tags[15] == "css"  # every 8th slot
    assert tags[:7] == ["kafka"] * 7
    (snapshot,) = conn.execute("select item_ids from public.feed_snapshots").fetchone()
    assert len(snapshot) > 20  # 20 candidates plus explore items


def test_hide_and_block_apply_to_the_next_page(world: World) -> None:
    conn = world.conn
    user = make_user(conn, "hide@example.com")
    follow(conn, user, ["backend", "ai", "react"])
    first = page(conn, user)
    upcoming = [r[2] for r in page(conn, user, first[-1][0], first[-1][1])]
    hidden, less = upcoming[0], upcoming[1]
    blocked_source = world.sources[upcoming[2]]
    blocked_tag = "react"

    call(conn, user, "select public.item_action(%s, 'hide')", (hidden,))
    call(conn, user, "select public.item_action(%s, 'less_like_this')", (less,))
    call(conn, user, "select public.set_source_blocked(%s)", (blocked_source,))
    call(conn, user, "select public.set_tag_state(%s, 'block')", (blocked_tag,))
    second = next_page(conn, user, first)

    ids = [r[2] for r in second]
    assert second[0][0] == first[0][0]  # same snapshot: no re-ranking
    assert hidden not in ids and less not in ids
    assert all(world.sources[i] != blocked_source for i in ids)
    assert all(world.items[i] != blocked_tag for i in ids)
    assert len(ids) == PAGE  # filled from further down the snapshot
    assert not set(ids) & {r[2] for r in first}

    # Unhide: the item can come back on a later ranking.
    call(conn, user, "select public.item_action(%s, 'hide', false)", (hidden,))
    assert conn.execute(
        "select count(*) from public.events where item_id = %s and kind = 'hide'", (hidden,)
    ).fetchone() == (0,)


def test_blocking_a_parent_tag_blocks_its_descendants(world: World) -> None:
    conn = world.conn
    user = make_user(conn, "parent@example.com")
    follow(conn, user, ["backend", "ai", "react"])
    call(conn, user, "select public.set_tag_state('backend', 'block')")

    rows = page(conn, user, refresh=True)

    assert not {world.items[r[2]] for r in rows} & BACKEND_FAMILY


def test_clicked_items_leave_the_next_ranking(world: World) -> None:
    conn = world.conn
    user = make_user(conn, "click@example.com")
    follow(conn, user, ["backend", "go", "rust"])
    first = page(conn, user)
    clicked = first[0][2]

    events = [{"item_id": clicked, "kind": "click"}]
    assert call(conn, user, "select public.record_events(%s)", (json.dumps(events),)) == [(1,)]
    # A fresh first page reuses the snapshot (younger than 30 min), so it is still there...
    assert clicked in [r[2] for r in page(conn, user)]
    # ...and a refresh re-ranks without it.
    refreshed = page(conn, user, refresh=True)
    assert refreshed[0][0] != first[0][0]
    assert clicked not in [r[2] for r in refreshed]


def test_stale_snapshot_is_recomputed_and_expired_cursor_restarts(world: World) -> None:
    conn = world.conn
    user = make_user(conn, "stale@example.com")
    follow(conn, user, ["backend", "go", "rust"])
    first = page(conn, user)
    snapshot = first[0][0]

    conn.execute(
        "update public.feed_snapshots set created_at = now() - interval '31 minutes' where id = %s",
        (snapshot,),
    )
    # A cursor into a 31-minute-old snapshot still works (pages stay stable)...
    assert next_page(conn, user, first)[0][:2] == (snapshot, PAGE + 1)
    # ...but a new first page ranks anew.
    fresh = page(conn, user)
    assert fresh[0][0] != snapshot

    conn.execute("delete from public.feed_snapshots where id = %s", (fresh[0][0],))
    restarted = next_page(conn, user, fresh)
    assert restarted[0][0] != fresh[0][0] and restarted[0][1] == 1


def test_feed_for_user_matches_my_feed(world: World) -> None:
    conn = world.conn
    user = make_user(conn, "tg@example.com")
    follow(conn, user, ["backend", "ai", "react"])

    digest = conn.execute("select id from public.feed_for_user(%s, 10)", (user,)).fetchall()
    feed = page(conn, user)

    assert [r[0] for r in digest] == [r[2] for r in feed[:10]]
    assert conn.execute("select count(*) from public.feed_snapshots").fetchone() == (1,)


# --- access control --------------------------------------------------------


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_clients_cannot_call_feed_for_user(world: World, role: str) -> None:
    conn = world.conn
    user = make_user(conn, "victim@example.com")
    with pytest.raises(InsufficientPrivilege), conn.transaction(), as_role(conn, role, user):
        conn.execute("select * from public.feed_for_user(%s, 10)", (user,))


def test_service_role_can_call_feed_for_user(world: World) -> None:
    conn = world.conn
    user = make_user(conn, "svc@example.com")
    with as_role(conn, "service_role"):
        rows = conn.execute("select id from public.feed_for_user(%s, 5)", (user,)).fetchall()
    assert len(rows) == 5


@pytest.mark.parametrize(
    "query",
    [
        "select * from public.my_feed()",
        "select public.item_action(%s, 'bookmark')",
        "select public.set_followed_tags(array['go', 'rust', 'ai'])",
        "select public.delete_my_history()",
    ],
)
def test_anon_cannot_call_user_rpcs(world: World, query: str) -> None:
    conn = world.conn
    params = (next(iter(world.items)),) if "%s" in query else ()
    with pytest.raises(InsufficientPrivilege), conn.transaction(), as_role(conn, "anon"):
        conn.execute(query, params)


def test_authenticated_without_user_is_rejected(world: World) -> None:
    conn = world.conn
    with pytest.raises(InsufficientPrivilege), conn.transaction(), as_role(conn, "authenticated"):
        conn.execute("select * from public.my_feed()")


USER_TABLES = (
    "profiles",
    "user_tags",
    "user_sources",
    "events",
    "impressions",
    "feed_snapshots",
    "tag_affinity",
)


@pytest.fixture
def two_users(world: World) -> tuple[str, str]:
    """Users A and B with a row in every user table."""
    conn = world.conn
    a = make_user(conn, "a@example.com")
    b = make_user(conn, "b@example.com")
    item = next(iter(world.items))
    for user in (a, b):
        follow(conn, user, ["backend", "go", "rust"], text=f"profile of {user}")
        call(conn, user, "select public.set_source_blocked(%s)", (world.sources[item],))
        call(conn, user, "select public.item_action(%s, 'bookmark')", (item,))
        rows = page(conn, user)
        call(conn, user, "select public.record_impression(%s, %s)", (rows[0][0], [rows[0][2]]))
        conn.execute(
            "insert into public.tag_affinity (user_id, tag, score) values (%s, 'kafka', 0.5)",
            (user,),
        )
    return a, b


@pytest.mark.parametrize("table", USER_TABLES)
def test_users_read_only_their_own_rows(
    world: World, two_users: tuple[str, str], table: str
) -> None:
    conn = world.conn
    a, b = two_users
    query = sql.SQL("select distinct user_id::text from public.{}").format(sql.Identifier(table))
    for user in (a, b):
        with as_user(conn, user):
            assert conn.execute(query).fetchall() == [(user,)]
    with as_role(conn, "anon"), pytest.raises(InsufficientPrivilege), conn.transaction():
        conn.execute(query)


@pytest.mark.parametrize("table", USER_TABLES)
@pytest.mark.parametrize("kind", ["insert", "update", "delete"])
def test_users_cannot_write_user_tables_directly(
    world: World, two_users: tuple[str, str], table: str, kind: str
) -> None:
    conn = world.conn
    a, _ = two_users
    ident = sql.Identifier(table)
    statements = {
        # Privileges are checked before NOT NULL and the other columns' constraints.
        "insert": sql.SQL("insert into public.{} (user_id) values ({})").format(
            ident, sql.Literal(a)
        ),
        "update": sql.SQL("update public.{} set user_id = user_id").format(ident),
        "delete": sql.SQL("delete from public.{}").format(ident),
    }
    with pytest.raises(InsufficientPrivilege), conn.transaction(), as_user(conn, a):
        conn.execute(statements[kind])


@pytest.mark.parametrize("column", ["user_vec", "model"])
def test_users_cannot_read_pipeline_columns(
    world: World, two_users: tuple[str, str], column: str
) -> None:
    conn = world.conn
    a, _ = two_users
    query = sql.SQL("select {} from public.profiles").format(sql.Identifier(column))
    with pytest.raises(InsufficientPrivilege), conn.transaction(), as_user(conn, a):
        conn.execute(query)


@pytest.mark.parametrize("table", ["invites", "ranking_config"])
@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_clients_cannot_read_private_tables(world: World, table: str, role: str) -> None:
    conn = world.conn
    user = make_user(conn, "curious@example.com")
    query = sql.SQL("select * from public.{}").format(sql.Identifier(table))
    with pytest.raises(InsufficientPrivilege), conn.transaction(), as_role(conn, role, user):
        conn.execute(query)


def test_users_cannot_use_another_users_snapshot(world: World, two_users: tuple[str, str]) -> None:
    conn = world.conn
    a, b = two_users
    (b_snapshot,) = conn.execute(
        "select id from public.feed_snapshots where user_id = %s", (b,)
    ).fetchone()

    rows = page(conn, a, b_snapshot, 5)

    assert rows[0][0] != b_snapshot and rows[0][1] == 1  # A's own list, from the top
    with pytest.raises(InvalidParameterValue), conn.transaction(), as_user(conn, a):
        conn.execute("select public.record_impression(%s, %s)", (b_snapshot, [rows[0][2]]))


def test_delete_my_history_only_touches_the_caller(
    world: World, two_users: tuple[str, str]
) -> None:
    conn = world.conn
    a, b = two_users
    item = next(iter(world.items))
    for user in (a, b):
        events = [
            {"item_id": item, "kind": "click"},
            {"item_id": item, "kind": "dwell", "dwell_ms": 45_000},
        ]
        call(conn, user, "select public.record_events(%s)", (json.dumps(events),))

    assert call(conn, a, "select public.delete_my_history()") == [(2,)]

    counts = conn.execute(
        "select user_id::text, kind, count(*) from public.events group by 1, 2 order by 1, 2"
    ).fetchall()
    assert {(k, n) for u, k, n in counts if u == a} == {("bookmark", 1)}
    assert {(k, n) for u, k, n in counts if u == b} == {("bookmark", 1), ("click", 1), ("dwell", 1)}
    for table in ("impressions", "feed_snapshots"):
        owners = conn.execute(
            sql.SQL("select distinct user_id::text from public.{}").format(sql.Identifier(table))
        ).fetchall()
        assert owners == [(b,)]


# --- user actions ----------------------------------------------------------


def test_reactions_bookmarks_and_history(world: World) -> None:
    conn = world.conn
    user = make_user(conn, "act@example.com")
    first, second, third = list(world.items)[:3]

    call(conn, user, "select public.item_action(%s, 'upvote')", (first,))
    call(conn, user, "select public.item_action(%s, 'downvote')", (first,))
    call(conn, user, "select public.item_action(%s, 'bookmark')", (first,))
    call(conn, user, "select public.item_action(%s, 'bookmark')", (first,))  # idempotent
    call(conn, user, "select public.item_action(%s, 'bookmark')", (second,))
    call(conn, user, "select public.item_action(%s, 'bookmark', false)", (first,))
    clicks = [{"item_id": i, "kind": "click"} for i in (third, second, third)]
    call(conn, user, "select public.record_events(%s)", (json.dumps(clicks),))

    kinds = call(conn, user, "select item_id, kind from public.events where kind <> 'click'")
    assert sorted(kinds) == sorted([(first, "downvote"), (second, "bookmark")])
    assert call(conn, user, "select id from public.my_bookmarks()") == [(second,)]
    assert {r[0] for r in call(conn, user, "select id from public.my_history()")} == {
        second,
        third,
    }


@pytest.mark.parametrize(
    ("query", "params"),
    [
        ("select public.item_action(%s, 'like')", ("item",)),
        ("select public.item_action(%s, 'upvote')", ("f" * 40,)),
        ("select public.set_followed_tags(%s)", (["backend", "go"],)),
        ("select public.set_followed_tags(%s)", (["backend", "go", "nope"],)),
        ("select public.set_tag_state('nope', 'follow')", ()),
        ("select public.set_tag_state('go', 'mute')", ()),
        ("select public.set_source_blocked(-1)", ()),
        ("select public.record_events('{}')", ()),
        ("select public.record_impression(null, '{}')", ()),
    ],
)
def test_invalid_actions_are_rejected(world: World, query: str, params: tuple) -> None:
    conn = world.conn
    user = make_user(conn, "bad@example.com")
    if params == ("item",):
        params = (next(iter(world.items)),)
    with pytest.raises(InvalidParameterValue), conn.transaction(), as_user(conn, user):
        conn.execute(query, params)


def test_record_events_skips_junk_and_caps_dwell(world: World) -> None:
    conn = world.conn
    user = make_user(conn, "events@example.com")
    item = next(iter(world.items))
    events = [
        {"item_id": item, "kind": "click"},
        {"item_id": item, "kind": "dwell", "dwell_ms": 99_000_000},
        {"item_id": item, "kind": "dwell"},  # no duration
        {"item_id": item, "kind": "upvote"},  # reactions go through item_action
        {"item_id": "f" * 40, "kind": "click"},  # unknown item
        "garbage",
    ]

    assert call(conn, user, "select public.record_events(%s)", (json.dumps(events),)) == [(2,)]
    assert call(conn, user, "select kind, dwell_ms from public.events order by id") == [
        ("click", None),
        ("dwell", 3_600_000),
    ]
    too_many = json.dumps([{"item_id": item, "kind": "click"}] * 51)
    with pytest.raises(InvalidParameterValue), conn.transaction(), as_user(conn, user):
        conn.execute("select public.record_events(%s)", (too_many,))


def test_event_rate_limit(world: World) -> None:
    conn = world.conn
    user = make_user(conn, "flood@example.com")
    item = next(iter(world.items))
    conn.execute(
        "insert into public.events (user_id, item_id, kind) "
        "select %s, %s, 'click' from generate_series(1, 590)",
        (user, item),
    )
    ten = json.dumps([{"item_id": item, "kind": "click"}] * 10)
    assert call(conn, user, "select public.record_events(%s)", (ten,)) == [(10,)]
    with pytest.raises(ProgramLimitExceeded), conn.transaction(), as_user(conn, user):
        conn.execute("select public.record_events(%s)", (ten,))


@pytest.mark.parametrize(
    ("kind", "dwell_ms"), [("click", 100), ("dwell", None), ("dwell", -1), ("view", None)]
)
def test_events_checks(world: World, kind: str, dwell_ms: int | None) -> None:
    conn = world.conn
    user = make_user(conn, "check@example.com")
    with pytest.raises(CheckViolation), conn.transaction():
        conn.execute(
            "insert into public.events (user_id, item_id, kind, dwell_ms) values (%s, %s, %s, %s)",
            (user, next(iter(world.items)), kind, dwell_ms),
        )


def test_onboarding_sets_tags_and_queues_the_profile_text(world: World) -> None:
    conn = world.conn
    user = make_user(conn, "onboard@example.com", {"user_name": "octo"})
    assert call(conn, user, "select display_name, profile_text from public.profiles") == [
        ("octo", None)
    ]
    conn.execute("insert into public.user_tags values (%s, 'ai', 'block')", (user,))

    follow(conn, user, ["backend", "ai", "go", "go"], text="  Java and Kafka  ")
    conn.execute(
        "update public.profiles set profile_embedded_at = now() where user_id = %s", (user,)
    )
    follow(conn, user, ["backend", "ai", "go"])  # nothing changed: stays embedded
    embedded = conn.execute("select profile_embedded_at is not null from public.profiles")
    assert embedded.fetchone() == (True,)
    # Other tags: user_vec holds the old centroid, so the text is queued again
    # (w4_profile_refresh).
    follow(conn, user, ["backend", "ai", "rust"])
    embedded = conn.execute("select profile_embedded_at is not null from public.profiles")
    assert embedded.fetchone() == (False,)

    call(conn, user, "select public.update_profile(p_profile_text => 'Go services')")

    assert call(conn, user, "select tag, state from public.user_tags order by tag") == [
        ("ai", "follow"),
        ("backend", "follow"),
        ("rust", "follow"),
    ]
    assert call(conn, user, "select profile_text, profile_embedded_at from public.profiles") == [
        ("Go services", None)
    ]


# --- sign-up -----------------------------------------------------------------


def hook(conn: psycopg.Connection, user: dict[str, Any]) -> dict[str, Any]:
    event = {"metadata": {"name": "before-user-created"}, "user": user}
    (result,) = conn.execute(
        "select public.before_user_created_hook(%s)", (json.dumps(event),)
    ).fetchone()
    return result


def github_user(login: str, email: str = "someone@users.noreply.github.com") -> dict[str, Any]:
    return {
        "email": email,
        "app_metadata": {"provider": "github", "providers": ["github"]},
        "user_metadata": {"user_name": login, "preferred_username": login},
    }


def email_user(email: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "email": email,
        "app_metadata": {"provider": "email", "providers": ["email"]},
        "user_metadata": meta or {},
    }


@pytest.fixture
def invites(pg_conn: psycopg.Connection) -> psycopg.Connection:
    pg_conn.execute(
        "insert into public.invites (email, github_login) values "
        "('Owner@Example.com', null), (null, 'OctoCat')"
    )
    return pg_conn


@pytest.mark.parametrize(
    "user",
    [
        email_user("owner@example.com"),
        github_user("someone-else", email="OWNER@example.com"),
        github_user("octocat"),
    ],
)
def test_hook_accepts_invited_email_or_github_login(
    invites: psycopg.Connection, user: dict[str, Any]
) -> None:
    assert hook(invites, user) == {}
    used = invites.execute("select count(*) from public.invites where used_at is not null")
    assert used.fetchone() == (1,)


@pytest.mark.parametrize(
    "user",
    [
        email_user("stranger@example.com"),
        github_user("stranger"),
        # user_metadata is client-controlled on email sign-ups: never trust a login there.
        email_user("stranger@example.com", {"user_name": "octocat"}),
        {"email": "", "app_metadata": {"provider": "phone"}, "user_metadata": {}},
    ],
)
def test_hook_rejects_everyone_else(invites: psycopg.Connection, user: dict[str, Any]) -> None:
    result = hook(invites, user)
    assert result["error"]["http_code"] == 403
    used = invites.execute("select count(*) from public.invites where used_at is not null")
    assert used.fetchone() == (0,)


def test_hook_is_executable_by_auth_only(pg_conn: psycopg.Connection) -> None:
    rows = pg_conn.execute(
        "select r, has_function_privilege(r, 'public.before_user_created_hook(jsonb)', 'execute') "
        "from unnest(array['supabase_auth_admin', 'anon', 'authenticated', 'service_role']) r"
    ).fetchall()
    assert dict(rows) == {
        "supabase_auth_admin": True,
        "anon": False,
        "authenticated": False,
        "service_role": False,
    }


def test_new_auth_user_gets_a_profile(pg_conn: psycopg.Connection) -> None:
    user = make_user(pg_conn, "new.person@example.com", {"full_name": "New Person"})
    row = pg_conn.execute(
        "select display_name, lang from public.profiles where user_id = %s", (user,)
    ).fetchone()
    assert row == ("New Person", "vi")
    pg_conn.execute("delete from auth.users where id = %s", (user,))
    assert pg_conn.execute("select count(*) from public.profiles").fetchone() == (0,)
