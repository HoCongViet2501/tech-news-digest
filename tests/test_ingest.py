"""`digest ingest` end to end on fixtures: the same flow on MemoryStore and, with
DIGEST_TEST_DB_URL, on the local Supabase Postgres (integration marker)."""

import asyncio
import json
from datetime import timedelta

import httpx
import pytest

from conftest import NOW, fixture_bytes
from digest.config import Config
from digest.http import make_client
from digest.ingest import FetchContext, IngestStats, fetch_sources, run_ingest
from digest.offline import offline_transport
from digest.store import (
    ContentStore,
    MemoryStore,
    PostgresStore,
    Source,
    SourcesFile,
    Stage,
    StoredItem,
)
from digest.store.sources_yaml import API_SOURCES

ETAG = '"lwn-v1"'
LWN_FEED = "https://lwn.net/headlines/rss"


class FakeWeb:
    """Fixture-backed web (like DIGEST_OFFLINE=1) plus the behaviors these tests need:
    an RSS feed with an ETag, a dead feed, a dead API, HN points that grow between runs,
    and real HTML for the LWN article pages so enrichment has something to find."""

    def __init__(self) -> None:
        self.hn_bonus = 0
        self.requests: list[httpx.Request] = []
        self._offline = offline_transport()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if (host, path) == ("lwn.net", "/headlines/rss"):
            if request.headers.get("If-None-Match") == ETAG:
                return httpx.Response(304, headers={"ETag": ETAG})
            return httpx.Response(200, content=fixture_bytes("rss_lwn.xml"), headers={"ETag": ETAG})
        if host == "lwn.net" and path.startswith("/Articles/"):
            return httpx.Response(
                200,
                content=fixture_bytes("enrich/og_tags.html"),
                headers={"Content-Type": "text/html; charset=utf-8"},
            )
        if host == "broken.example":
            return httpx.Response(500)
        if host == "programming.dev":  # lemmy
            return httpx.Response(503)
        if (host, path) == ("hn.algolia.com", "/api/v1/search_by_date"):
            data = json.loads(fixture_bytes("hackernews.json"))
            for hit in data["hits"]:
                hit["points"] = (hit.get("points") or 0) + self.hn_bonus
            return httpx.Response(200, json=data)
        return self._offline.handle_request(request)

    def count(self, host: str, **params: str) -> int:
        return sum(
            1
            for r in self.requests
            if r.url.host == host and all(r.url.params.get(k) == v for k, v in params.items())
        )


SPECS = SourcesFile.model_validate(
    {
        "devto": {"tags": ["java", "go"]},
        "rss": [
            {"name": "LWN", "url": LWN_FEED},
            {"name": "Broken", "url": "https://broken.example/feed"},
        ],
        "api": [{"kind": kind, "cadence_hours": 3} for kind in API_SOURCES],
    }
).specs()


@pytest.fixture(
    params=[
        pytest.param("memory"),
        pytest.param("postgres", marks=pytest.mark.integration),
    ]
)
def store(request: pytest.FixtureRequest) -> ContentStore:
    if request.param == "memory":
        return MemoryStore()
    return PostgresStore(request.getfixturevalue("pg_conn"))


@pytest.fixture
def cfg() -> Config:
    return Config.model_validate(
        {"profile": "Backend developer.", "ingest": {"enrich_per_run": 1000}}
    )


def ingest(store: ContentStore, cfg: Config, web: FakeWeb, now=NOW) -> IngestStats:
    async def go() -> IngestStats:
        async with make_client(httpx.MockTransport(web)) as client:
            return await run_ingest(store, cfg, SPECS, client, now=now, devto_pause_seconds=0)

    return asyncio.run(go())


def all_items(store: ContentStore) -> list[StoredItem]:
    return store.pending(Stage.TAG, limit=100_000)  # nothing is tagged in W1


def by_name(store: ContentStore) -> dict[str, Source]:
    return {s.name: s for s in store.list_sources()}


def test_first_run_stores_items_and_source_health(store: ContentStore, cfg: Config) -> None:
    web = FakeWeb()

    stats = ingest(store, cfg, web)

    items = all_items(store)
    assert stats.due == len(SPECS)
    assert stats.new == len(items) > 100
    assert stats.fetched >= stats.new
    assert (stats.refreshed, stats.not_modified) == (0, 0)
    # The dead feed and the dead API fail; everything else still lands.
    assert stats.errors == 2
    sources = by_name(store)
    assert "500" in (sources["Broken"].last_error or "")
    assert "503" in (sources["Lemmy programming.dev"].last_error or "")
    assert sources["Broken"].last_ok_at is None
    ok = [s for s in sources.values() if s.name not in ("Broken", "Lemmy programming.dev")]
    assert all(s.last_ok_at == NOW and s.last_error is None for s in ok)
    assert sources["LWN"].etag == ETAG
    # Offline, every dev.to tag serves the same fixture: the second tag's items are
    # already known from the first one and stay attributed to it.
    go = sources["dev.to #go"].id
    assert {i.source_id for i in items} == {s.id for s in ok} - {go}
    # dev.to's overall top list is fetched once, with the first tag.
    assert web.count("dev.to", top="1") == 1
    assert web.count("dev.to", tag="java") == web.count("dev.to", tag="go") == 1


def test_enrichment_marks_every_item_done(store: ContentStore, cfg: Config) -> None:
    stats = ingest(store, cfg, FakeWeb())

    items = all_items(store)
    assert stats.enriched == len(items)
    assert store.pending(Stage.ENRICH, limit=100_000) == []
    assert all(i.enriched_at == NOW for i in items)
    lwn = [i for i in items if i.url.startswith("https://lwn.net/Articles/")]
    assert lwn
    for item in lwn:
        assert item.image_url == "https://cdn.example.com/covers/autovacuum.png"
        assert item.lang == "en"
        assert item.reading_min is not None
        # The feed already gave a description; enrichment does not replace it.
        assert item.description and "autovacuum" not in item.description
    # Pages that 404 are still marked done, with nothing filled in.
    others = [i for i in items if i.url.startswith("https://github.com/")]
    assert others and all(i.image_url is None and i.enriched_at == NOW for i in others)


def test_pop_pct_is_a_per_source_percentile(store: ContentStore, cfg: Config) -> None:
    ingest(store, cfg, FakeWeb())

    items = all_items(store)
    scored = [i for i in items if i.pop_pct is not None]
    assert scored
    assert all(0 <= i.pop_pct <= 1 for i in scored)  # type: ignore[operator]
    hn_id = by_name(store)["Hacker News"].id
    hn = sorted((i for i in scored if i.source_id == hn_id), key=lambda i: i.native_score)
    assert len(hn) > 10
    pcts = [i.pop_pct for i in hn]
    assert pcts == sorted(pcts)  # follows native_score
    assert pcts[0] < 0.05 and pcts[-1] > 0.95
    # RSS has no votes (score = base weight), so every item ties at the middle.
    lwn_id = by_name(store)["LWN"].id
    lwn = [i for i in items if i.source_id == lwn_id and i.published_at >= NOW - timedelta(7)]
    assert lwn and all(i.pop_pct == pytest.approx(0.5) for i in lwn)


def test_second_run_refreshes_scores_without_duplicates(store: ContentStore, cfg: Config) -> None:
    web = FakeWeb()
    ingest(store, cfg, web)
    before = {i.id: i for i in all_items(store)}
    hn_id = by_name(store)["Hacker News"].id

    # Too early: nothing is due, nothing is fetched.
    early = ingest(store, cfg, web, now=NOW + timedelta(hours=1))
    assert (early.due, early.fetched, early.new) == (0, 0, 0)

    web.hn_bonus = 100
    later = NOW + timedelta(hours=25)
    stats = ingest(store, cfg, web, now=later)

    after = {i.id: i for i in all_items(store)}
    assert stats.due == len(SPECS)
    assert stats.new == 0
    assert after.keys() == before.keys()
    assert stats.refreshed > 0
    assert stats.not_modified == 1  # LWN answered 304 to the stored ETag
    lwn = by_name(store)["LWN"]
    assert (lwn.etag, lwn.last_ok_at, lwn.last_error) == (ETAG, later, None)
    hn = [i for i in after.values() if i.source_id == hn_id]
    assert hn and all(i.native_score == before[i.id].native_score + 100 for i in hn)
    assert all(i.fetched_at == later for i in hn)
    assert all(i.first_seen_at == NOW for i in after.values())
    assert stats.errors == 2


def test_conditional_get_sends_stored_etag(store: ContentStore, cfg: Config) -> None:
    web = FakeWeb()
    ingest(store, cfg, web)
    ingest(store, cfg, web, now=NOW + timedelta(hours=25))

    feeds = [r for r in web.requests if r.url == LWN_FEED]
    assert [r.headers.get("If-None-Match") for r in feeds] == [None, ETAG]


def test_killed_run_is_finished_by_next_run(
    store: ContentStore, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("killed during enrichment")

    monkeypatch.setattr("digest.ingest.enrich_many", crash)
    with pytest.raises(RuntimeError):
        ingest(store, cfg, FakeWeb())
    stored = all_items(store)
    assert stored
    assert len(store.pending(Stage.ENRICH, limit=100_000)) == len(stored)

    monkeypatch.undo()
    stats = ingest(store, cfg, FakeWeb(), now=NOW + timedelta(minutes=5))

    # Sources were recorded before the crash, so nothing is refetched...
    assert (stats.due, stats.new) == (0, 0)
    # ...but the enrich queue left behind is drained, and pop_pct is computed.
    assert stats.enriched == len(stored)
    assert store.pending(Stage.ENRICH, limit=100_000) == []
    assert any(i.pop_pct is not None for i in all_items(store))


def test_store_failure_for_one_source_does_not_stop_the_run(
    store: ContentStore, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.sync_sources(SPECS)
    lobsters_id = by_name(store)["Lobsters"].id
    real_upsert = store.upsert_items

    def flaky(items):  # type: ignore[no-untyped-def]
        if items and items[0].source_id == lobsters_id:
            raise ValueError("boom")
        return real_upsert(items)

    monkeypatch.setattr(store, "upsert_items", flaky)
    stats = ingest(store, cfg, FakeWeb())

    sources = by_name(store)
    assert sources["Lobsters"].last_error == "store: ValueError"
    assert sources["Hacker News"].last_error is None
    assert stats.errors == 3
    assert stats.new > 0


def test_fetch_respects_per_host_and_overall_limits(cfg: Config) -> None:
    in_flight: dict[str, int] = {}
    peak: dict[str, int] = {}
    total_peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal total_peak
        host = request.url.host
        in_flight[host] = in_flight.get(host, 0) + 1
        peak[host] = max(peak.get(host, 0), in_flight[host])
        total_peak = max(total_peak, sum(in_flight.values()))
        await asyncio.sleep(0.01)
        in_flight[host] -= 1
        return httpx.Response(200, content=fixture_bytes("rss_lwn.xml"))

    store = MemoryStore()
    store.sync_sources(
        SourcesFile.model_validate(
            {
                "rss": [
                    {"name": f"{host}{n}", "url": f"https://{host}.example/{n}"}
                    for host in "abcdef"
                    for n in range(4)
                ]
            }
        ).specs()
    )
    small = cfg.model_copy(
        update={"ingest": cfg.ingest.model_copy(update={"concurrency": 5, "per_host": 2})}
    )

    async def go() -> None:
        async with make_client(httpx.MockTransport(handler)) as client:
            ctx = FetchContext(cfg=small, client=client, now=NOW)
            results = await fetch_sources(store.list_sources(), ctx)
            assert [s.id for s, _ in results] == [s.id for s in store.list_sources()]
            assert all(r.outcome.ok for _, r in results)

    asyncio.run(go())
    assert max(peak.values()) == 2
    assert total_peak <= 5


def test_stats_line() -> None:
    stats = IngestStats(
        due=3, fetched=10, new=4, refreshed=6, not_modified=1, errors=1, enriched=4, duration_s=2.25
    )
    assert stats.line() == (
        "ingest due=3 fetched=10 new=4 refreshed=6 not_modified=1 errors=1 enriched=4 duration=2.2s"
    )


def test_rss_entries_older_than_max_age_are_skipped(store: ContentStore, cfg: Config) -> None:
    short = cfg.model_copy(update={"ingest": cfg.ingest.model_copy(update={"rss_max_age_days": 2})})

    ingest(store, short, FakeWeb())

    lwn_id = by_name(store)["LWN"].id
    lwn = [i for i in all_items(store) if i.source_id == lwn_id]
    assert lwn
    assert all(i.published_at >= NOW - timedelta(days=2) for i in lwn)
    # The fixture also has older entries, which the default 30 days would keep.
    assert len(lwn) < 15
