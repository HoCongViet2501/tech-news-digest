"""Profile embedding (profiles.user_vec) and activity retention: unit tests on MemoryStore,
integration tests against the migrated Postgres (local Supabase)."""

# F811: the imported `world` fixture is requested as a parameter of the same name.
# ruff: noqa: F811

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import numpy as np
import psycopg
import pytest
from test_personalization import World, as_role, make_user, world  # noqa: F401

from digest.config import Config
from digest.embedding import HashEmbedder
from digest.http import make_client
from digest.ingest import IngestStats, run_ingest
from digest.profiles import embed_profiles, profile_vector
from digest.store import MemoryStore, PendingProfile, PostgresStore
from digest.store.memory import MemoryProfile

T0 = datetime(2026, 11, 15, 7, 0, tzinfo=UTC)
DIM = 384


def unit(*hot: int) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    for d in hot:
        v[d] = 1.0
    return v / np.linalg.norm(v)


# --- profile_vector ------------------------------------------------------------


def test_without_centroid_the_text_vector_is_used_alone() -> None:
    text = unit(1, 2)
    assert np.allclose(profile_vector(text, None, 0.5), text)


def test_equal_weights_pull_equally_towards_text_and_centroid() -> None:
    text, centroid = unit(1), unit(2)
    vec = profile_vector(text, centroid, 0.5)
    assert np.isclose(np.linalg.norm(vec), 1.0)
    assert np.isclose(vec @ text, vec @ centroid)
    assert vec @ text > 0.7


def test_weight_shifts_the_mix_and_inputs_are_normalized_first() -> None:
    text, centroid = unit(1), unit(2) * 5  # an unnormalized centroid still counts as unit
    vec = profile_vector(text, centroid, 0.8)
    assert vec @ text > vec @ (centroid / 5)
    assert np.allclose(profile_vector(text, centroid, 1.0), text)
    assert np.allclose(profile_vector(text, centroid, 0.0), unit(2))


def test_opposite_inputs_fall_back_to_the_text() -> None:
    text = unit(1)
    assert np.allclose(profile_vector(text, -text, 0.5), text)


# --- embed_profiles on MemoryStore ----------------------------------------------


def cfg_with(**profiles: object) -> Config:
    return Config.model_validate({"profile": "x", "ingest": {"profiles": profiles}})


def add_profile(store: MemoryStore, text: str | None, minutes: int = 0) -> uuid.UUID:
    user = uuid.uuid4()
    store.profiles[user] = MemoryProfile(text=text, updated_at=T0 + timedelta(minutes=minutes))
    return user


def test_embeds_pending_profiles_with_and_without_followed_tags() -> None:
    store = MemoryStore(clock=lambda: T0)
    embedder = HashEmbedder()
    with_tags = add_profile(store, "Java Kafka microservices")
    alone = add_profile(store, "Rust compilers", minutes=1)
    no_text = add_profile(store, None)
    store.follow_centroids[with_tags] = unit(5).tolist()
    store.follow_centroids[no_text] = unit(6).tolist()

    assert embed_profiles(store, embedder, cfg_with(), T0) == 2

    text_vec = embedder.embed(["Java Kafka microservices"])[0]
    want = profile_vector(text_vec, unit(5), 0.5)
    got = np.array(store.profiles[with_tags].user_vec)
    assert got @ want > 0.999  # halfvec rounding
    assert store.profiles[with_tags].embedded_at == T0
    assert np.array(store.profiles[alone].user_vec) @ embedder.embed(["Rust compilers"])[0] > 0.999
    assert store.profiles[no_text].user_vec is None
    assert store.profiles[no_text].embedded_at is None
    # Done profiles are not taken again.
    assert embed_profiles(store, embedder, cfg_with(), T0) == 0


def test_per_run_caps_the_batch_oldest_first() -> None:
    store = MemoryStore()
    users = [add_profile(store, f"text {n}", minutes=n) for n in range(3)]

    assert embed_profiles(store, HashEmbedder(), cfg_with(per_run=2), T0) == 2
    assert [store.profiles[u].embedded_at is not None for u in users] == [True, True, False]
    assert embed_profiles(store, HashEmbedder(), cfg_with(per_run=0), T0) == 0


class EditedMeanwhile(MemoryStore):
    """The user saves new text after the run read the queue."""

    def pending_profiles(self, limit: int) -> list[PendingProfile]:
        pending = super().pending_profiles(limit)
        for p in pending:
            self.profiles[p.user_id].updated_at += timedelta(seconds=1)
        return pending


def test_a_profile_edited_meanwhile_stays_pending() -> None:
    store = EditedMeanwhile()
    user = add_profile(store, "Go")

    assert embed_profiles(store, HashEmbedder(), cfg_with(), T0) == 0
    assert store.profiles[user].user_vec is None
    assert store.profiles[user].embedded_at is None


def test_logs_carry_counts_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    store = MemoryStore()
    user = add_profile(store, "Secret interests in Kafka")
    store.follow_centroids[user] = unit(3).tolist()

    embed_profiles(store, HashEmbedder(), cfg_with(), T0)

    assert "profiles: 1 embedded (1 with followed tags, 0 without)" in caplog.text
    assert str(user) not in caplog.text
    assert "Secret" not in caplog.text
    pending = PendingProfile(user_id=user, text="Secret", updated_at=T0)
    assert str(user) not in repr(pending) and "Secret" not in repr(pending)


def test_prune_activity_on_memory_store() -> None:
    store = MemoryStore()
    store.impressions = [T0 - timedelta(days=31), T0 - timedelta(days=29)]
    store.feed_snapshots = [T0 - timedelta(hours=25), T0 - timedelta(hours=1)]

    result = store.prune_activity(
        impressions_before=T0 - timedelta(days=30), snapshots_before=T0 - timedelta(days=1)
    )

    assert (result.impressions, result.feed_snapshots) == (1, 1)
    assert store.impressions == [T0 - timedelta(days=29)]
    assert store.feed_snapshots == [T0 - timedelta(hours=1)]


def test_ingest_run_prunes_activity_and_embeds_profiles() -> None:
    store = MemoryStore(clock=lambda: T0)
    user = add_profile(store, "Kafka")
    store.impressions = [T0 - timedelta(days=31)]
    store.feed_snapshots = [T0 - timedelta(days=2), T0]
    cfg = Config.model_validate({"profile": "x", "retention": {"impressions_days": 30}})

    async def go() -> IngestStats:
        async with make_client(httpx.MockTransport(lambda r: httpx.Response(404))) as client:
            return await run_ingest(store, cfg, [], client, now=T0, embedder=HashEmbedder())

    stats = asyncio.run(go())

    assert stats.profiles == 1
    assert store.profiles[user].embedded_at == T0
    assert store.impressions == []
    assert store.feed_snapshots == [T0]


# --- Postgres ------------------------------------------------------------------


def onboard(conn: psycopg.Connection, user: str, tags: list[str], text: str | None) -> None:
    with as_role(conn, "authenticated", user):
        conn.execute("select public.set_followed_tags(%s, %s)", (tags, text))


def profile_row(conn: psycopg.Connection, user: str) -> tuple:
    return conn.execute(
        "select profile_embedded_at, user_vec::extensions.vector::real[] "
        "from public.profiles where user_id = %s",
        (user,),
    ).fetchone()


@pytest.mark.integration
def test_pending_profiles_come_with_their_follow_centroid(world: World) -> None:
    conn = world.conn
    store = PostgresStore(conn)
    tagged = make_user(conn, "a@example.com")
    onboard(conn, tagged, ["kafka", "databases", "go"], "Event streaming at scale")
    no_text = make_user(conn, "b@example.com")
    onboard(conn, no_text, ["react", "css", "rust"], None)
    text_only = make_user(conn, "c@example.com")
    conn.execute(
        "update public.profiles set profile_text = 'Compilers' where user_id = %s", (text_only,)
    )

    pending = {str(p.user_id): p for p in store.pending_profiles(10)}

    assert set(pending) == {tagged, text_only}
    assert pending[text_only].centroid is None
    centroid = np.array(pending[tagged].centroid)
    (expected,) = conn.execute(
        "select public.follow_centroid(%s)::extensions.vector::real[]", (tagged,)
    ).fetchone()
    assert np.allclose(centroid, expected)
    assert len(store.pending_profiles(1)) == 1
    assert store.pending_profiles(0) == []


@pytest.mark.integration
def test_embed_profiles_sets_user_vec_and_the_ranking_uses_it(world: World) -> None:
    conn = world.conn
    store = PostgresStore(conn)
    user = make_user(conn, "a@example.com")
    onboard(conn, user, ["kafka", "databases", "go"], "Event streaming at scale")
    cfg = Config.model_validate({"profile": "x"})
    now = datetime.now(UTC)
    embedder = HashEmbedder()
    (centroid,) = conn.execute(
        "select public.follow_centroid(%s)::extensions.vector::real[]", (user,)
    ).fetchone()

    assert embed_profiles(store, embedder, cfg, now) == 1

    embedded_at, user_vec = profile_row(conn, user)
    assert embedded_at == now
    want = profile_vector(embedder.embed(["Event streaming at scale"])[0], centroid, 0.5)
    assert np.array(user_vec) @ want > 0.999
    assert store.pending_profiles(10) == []
    # feed_for_user now ranks with user_vec (interest_tag_weight, not cold start) and the
    # followed topics still lead.
    cards = store.feed_for_user(uuid.UUID(user), 10)
    assert {world.items[c.id] for c in cards[:5]} <= {"kafka", "databases"}


@pytest.mark.integration
def test_follow_changes_and_cleared_text_requeue_the_profile(world: World) -> None:
    conn = world.conn
    store = PostgresStore(conn)
    user = make_user(conn, "a@example.com")
    onboard(conn, user, ["kafka", "databases", "go"], "Streams")
    cfg = Config.model_validate({"profile": "x"})
    assert embed_profiles(store, HashEmbedder(), cfg, datetime.now(UTC)) == 1

    # Blocking a tag that was not followed leaves the vector alone.
    with as_role(conn, "authenticated", user):
        conn.execute("select public.set_tag_state('ai', 'block')")
    assert profile_row(conn, user)[0] is not None

    # Following another tag re-queues it (the centroid changed).
    with as_role(conn, "authenticated", user):
        conn.execute("select public.set_tag_state('react', 'follow')")
    embedded_at, user_vec = profile_row(conn, user)
    assert embedded_at is None and user_vec is not None
    assert len(store.pending_profiles(10)) == 1
    assert embed_profiles(store, HashEmbedder(), cfg, datetime.now(UTC)) == 1

    # Unfollowing does too.
    with as_role(conn, "authenticated", user):
        conn.execute("select public.set_tag_state('react', null)")
    assert profile_row(conn, user)[0] is None
    assert embed_profiles(store, HashEmbedder(), cfg, datetime.now(UTC)) == 1

    # Clearing the text drops the vector: the ranking goes back to the live centroid.
    with as_role(conn, "authenticated", user):
        conn.execute("select public.update_profile(p_profile_text => '')")
    assert profile_row(conn, user) == (None, None)
    assert store.pending_profiles(10) == []


@pytest.mark.integration
def test_a_profile_changed_after_it_was_read_is_not_saved(world: World) -> None:
    conn = world.conn
    store = PostgresStore(conn)
    user = make_user(conn, "a@example.com")
    onboard(conn, user, ["kafka", "databases", "go"], "Streams")
    [fresh] = store.pending_profiles(10)
    # Everything here shares one transaction (one now()), so a read from before the
    # user's edit is simulated by an older updated_at.
    stale = fresh.model_copy(update={"updated_at": fresh.updated_at - timedelta(minutes=1)})
    vec = profile_vector(HashEmbedder().embed(["Streams"])[0], fresh.centroid, 0.5)

    assert not store.save_profile_vector(stale, vec.tolist(), datetime.now(UTC))
    assert profile_row(conn, user) == (None, None)
    assert store.save_profile_vector(fresh, vec.tolist(), datetime.now(UTC))


@pytest.mark.integration
def test_prune_activity_deletes_old_impressions_and_snapshots(world: World) -> None:
    conn = world.conn
    store = PostgresStore(conn)
    user = make_user(conn, "a@example.com")
    now = datetime.now(UTC)
    for age in (timedelta(hours=1), timedelta(days=2)):
        (snap,) = conn.execute(
            "insert into public.feed_snapshots (user_id, item_ids, created_at) "
            "values (%s, '{a}', %s) returning id",
            (user, now - age),
        ).fetchone()
        conn.execute(
            "insert into public.impressions (user_id, snapshot_id, item_ids, created_at) "
            "values (%s, %s, '{a}', %s), (%s, %s, '{a}', %s)",
            (user, snap, now - age, user, snap, now - age - timedelta(days=30)),
        )

    result = store.prune_activity(
        impressions_before=now - timedelta(days=30), snapshots_before=now - timedelta(days=1)
    )

    assert (result.impressions, result.feed_snapshots) == (2, 1)
    assert conn.execute("select count(*) from public.feed_snapshots").fetchone() == (1,)
    left = conn.execute("select count(*), count(snapshot_id) from public.impressions").fetchone()
    assert left == (2, 1)  # the 2-day-old impression lost its (pruned) snapshot
