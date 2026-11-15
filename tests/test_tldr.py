"""Ingest step 9: TL;DR for the Popular pool (digest.tldr), on MemoryStore with a fake web."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from conftest import fixture_bytes
from digest.ai.fake import PREFIX, fake_chain
from digest.ai.llm import LLMChain, Provider
from digest.config import Config
from digest.http import make_client
from digest.store import MemoryStore, NewItem, SourceSpec, Stage, StoredItem
from digest.tldr import TldrStats, tldr_pending
from fakes import FakeClient, rate_limited

T0 = datetime(2026, 11, 15, 7, 0, tzinfo=UTC)
LONG = "A description long enough to stand in for the article when the page is unusable. " * 2


class Web:
    """good.example serves a real article; blocked.example disallows us in robots.txt;
    dead.example answers 500. Every other robots.txt is missing (allow all)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if path == "/robots.txt":
            if host == "blocked.example":
                return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
            return httpx.Response(404)
        if host == "good.example":
            return httpx.Response(
                200,
                content=fixture_bytes("article_lwn.html"),
                headers={"Content-Type": "text/html; charset=utf-8"},
            )
        return httpx.Response(500)

    def pages(self, host: str) -> int:
        return sum(1 for r in self.requests if r.url.host == host and r.url.path != "/robots.txt")


def make_cfg(**tldr: object) -> Config:
    settings = {"calls_per_run": 2, "batch_size": 2, "max_age_hours": 72} | tldr
    return Config.model_validate({"profile": "Backend developer.", "ingest": {"tldr": settings}})


def seed(
    store: MemoryStore, *items: tuple[str, str | None], first_seen: datetime = T0
) -> list[str]:
    """One item per (url, description); earlier ones rank higher (higher native_score)."""
    store.sync_sources([SourceSpec(kind="rss", name="Alpha", url="https://alpha.example/feed")])
    sid = store.list_sources()[0].id
    rows = [
        NewItem(
            id=f"{n:040x}",
            source_id=sid,
            title=f"Item {n}",
            url=url,
            description=description,
            published_at=T0 - timedelta(hours=1),
            fetched_at=first_seen,
            native_score=100 - n,
        )
        for n, (url, description) in enumerate(items, 1)
    ]
    store.upsert_items(rows)
    store.recompute_pop_pct(T0, timedelta(days=7))
    return [r.id for r in rows]


def run(
    store: MemoryStore, cfg: Config, chain: LLMChain | None, web: Web | None = None
) -> TldrStats:
    async def go() -> TldrStats:
        async with make_client(httpx.MockTransport(web or Web())) as client:
            return await tldr_pending(store, cfg, client, chain, now=T0)

    return asyncio.run(go())


def get(store: MemoryStore, item_id: str) -> StoredItem:
    return store.get_items([item_id])[0]


def answers(*pairs: tuple[str, str]) -> str:
    return json.dumps({"items": [{"id": i, "tldr": t} for i, t in pairs]})


def test_stores_tldrs_from_page_text_or_description_and_skips_thin_items() -> None:
    store = MemoryStore()
    web = Web()
    good, blocked, thin = seed(
        store,
        ("https://good.example/a", None),
        ("https://blocked.example/b", LONG),  # robots.txt says no: the description is used
        ("https://dead.example/c", "Too short."),  # page fails and the description is thin
    )
    client = FakeClient(answers((good, "Bản GDB 18.1 đã ra mắt."), (blocked, "Mô tả ngắn.")))

    stats = run(store, make_cfg(input_chars=500), LLMChain([Provider("p", "m", client)]), web)

    assert (stats.stored, stats.skipped, stats.calls, stats.waiting) == (2, 1, 1, 0)
    assert get(store, good).tldr == "Bản GDB 18.1 đã ra mắt."
    assert get(store, blocked).tldr == "Mô tả ngắn."
    assert (get(store, thin).tldr, get(store, thin).summarized_at) == (None, T0)
    assert all(get(store, i).summarized_at == T0 for i in (good, blocked))
    assert web.pages("blocked.example") == 0  # never fetched against robots.txt
    sent = json.loads(client.calls[0]["messages"][0]["content"].rsplit("\nItems:\n", 1)[1])
    assert "GDB interactive debugger" in sent[0]["content"]
    assert len(sent[0]["content"]) <= 500  # capped per item
    assert sent[1]["content"] == " ".join(LONG.split())
    # Nothing but the TL;DR was written: the article text is not stored anywhere.
    assert get(store, good).description is None


def test_offline_fake_chain_fills_the_pool(cfg: Config) -> None:
    store = MemoryStore()
    ids = seed(store, *[(f"https://good.example/{n}", None) for n in range(3)])

    stats = run(store, make_cfg(), fake_chain(cfg.ai))

    assert stats.stored == 3
    assert all((get(store, i).tldr or "").startswith(PREFIX) for i in ids)


def test_per_run_cap_leaves_the_rest_for_the_next_run(cfg: Config) -> None:
    store = MemoryStore()
    ids = seed(store, *[(f"https://good.example/{n}", None) for n in range(7)])
    capped = make_cfg(calls_per_run=2, batch_size=2)

    first = run(store, capped, fake_chain(cfg.ai))

    assert (first.stored, first.calls, first.waiting) == (4, 2, 3)
    assert [get(store, i).tldr is not None for i in ids] == [True] * 4 + [False] * 3  # by rank
    second = run(store, capped, fake_chain(cfg.ai))
    assert (second.stored, second.waiting) == (3, 0)


def test_only_the_pool_gets_a_tldr(cfg: Config) -> None:
    store = MemoryStore()
    ids = seed(store, *[(f"https://good.example/{n}", None) for n in range(4)])

    stats = run(store, make_cfg(pool_size=2, calls_per_run=5), fake_chain(cfg.ai))

    assert stats.stored == 2
    assert [get(store, i).summarized_at is not None for i in ids] == [True, True, False, False]


def test_llm_failure_leaves_items_queued_and_stops_the_stage() -> None:
    store = MemoryStore()
    ids = seed(store, *[(f"https://good.example/{n}", None) for n in range(4)])
    client = FakeClient("not json", "not json")

    stats = run(store, make_cfg(), LLMChain([Provider("p", "m", client)]))

    assert (stats.stored, stats.skipped, stats.calls, stats.waiting) == (0, 0, 1, 4)
    assert len(client.calls) == 1  # the second batch is not tried
    assert all(get(store, i).summarized_at is None for i in ids)


def test_waiting_items_give_up_after_max_age(cfg: Config) -> None:
    store = MemoryStore()
    old = seed(store, ("https://good.example/old", None), first_seen=T0 - timedelta(hours=73))
    web = Web()

    stats = run(store, make_cfg(max_age_hours=72), fake_chain(cfg.ai), web)

    assert (stats.stored, stats.skipped, stats.calls) == (0, 1, 0)
    assert (get(store, old[0]).tldr, get(store, old[0]).summarized_at) == (None, T0)
    assert web.requests == []  # no page fetched for it


def test_quota_error_stops_the_stage_even_when_a_fallback_answers() -> None:
    store = MemoryStore()
    ids = seed(store, *[(f"https://good.example/{n}", None) for n in range(4)])
    gemini = FakeClient(rate_limited(), rate_limited())
    groq = FakeClient(answers((ids[0], "Một."), (ids[1], "Hai.")), answers((ids[2], "Ba.")))
    chain = LLMChain([Provider("gemini", "m", gemini), Provider("groq", "m", groq)])

    stats = run(store, make_cfg(), chain)

    # The first batch is kept; the second is never sent to anyone.
    assert (stats.stored, stats.calls, stats.waiting) == (2, 1, 2)
    assert (len(gemini.calls), len(groq.calls)) == (1, 1)
    assert [get(store, i).tldr for i in ids] == ["Một.", "Hai.", None, None]


def test_quota_error_earlier_in_the_run_skips_the_stage() -> None:
    store = MemoryStore()
    seed(store, ("https://good.example/a", None))
    client = FakeClient()
    chain = LLMChain([Provider("gemini", "m", client)])
    chain.rate_limited.add("gemini")  # e.g. the tagger hit the quota first
    web = Web()

    stats = run(store, make_cfg(), chain, web)

    assert (stats.stored, stats.calls, stats.waiting) == (0, 0, 1)
    assert client.calls == [] and web.requests == []


def test_empty_answers_are_skipped_and_missing_ones_wait() -> None:
    store = MemoryStore()
    ids = seed(store, *[(f"https://good.example/{n}", None) for n in range(2)])
    client = FakeClient(answers((ids[0], "  ")))  # ids[1] left out of the answer

    stats = run(store, make_cfg(), LLMChain([Provider("p", "m", client)]))

    assert (stats.stored, stats.skipped, stats.waiting) == (0, 1, 1)
    assert (get(store, ids[0]).tldr, get(store, ids[0]).summarized_at) == (None, T0)
    assert get(store, ids[1]).summarized_at is None


@pytest.mark.parametrize("cap", [{"calls_per_run": 0}, {"pool_size": 0}])
def test_stage_off_without_budget_or_chain(cfg: Config, cap: dict[str, int]) -> None:
    store = MemoryStore()
    seed(store, ("https://good.example/a", None))

    assert run(store, make_cfg(**cap), fake_chain(cfg.ai)) == TldrStats()
    assert run(store, make_cfg(), None) == TldrStats()
    assert store.pending(Stage.SUMMARIZE, 10)[0].summarized_at is None
