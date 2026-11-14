"""`python -m digest ingest`: one content-store run (docs/PLAN.md §13 Ingestion).

Steps 1-4, 8 and 10 of the plan: sync sources.yaml, fetch the due sources, normalize
and upsert, enrich pending items, recompute pop_pct, record source health. Tagging,
embeddings, LLM, clustering and TL;DR come in later phases.

Every step is resumable: items are upserted by id, record_fetch runs only after a
source's items are stored (so a crash leaves the source due), and enrichment works off
the `enriched_at` queue, so whatever a killed run left undone is picked up by the next.
"""

import asyncio
import logging
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from digest.config import Config
from digest.enrich import Enrichment, RobotsCache, enrich_many
from digest.fetchers import (
    devto,
    github_search,
    github_trending,
    hackernews,
    hf_papers,
    lemmy,
    lobsters,
    producthunt_rss,
    rss,
    viblo,
)
from digest.models import Item
from digest.normalize import normalize
from digest.store import ContentStore, EnrichmentUpdate, FetchOutcome, NewItem, Source, Stage
from digest.store.models import SourceSpec

log = logging.getLogger(__name__)

ERROR_MAX_CHARS = 300


class IngestStats(BaseModel):
    due: int = 0  # sources due this run
    fetched: int = 0  # items returned by the fetchers, after URL dedupe per source
    new: int = 0
    refreshed: int = 0  # known items whose score/comments were refreshed
    not_modified: int = 0  # feeds that answered 304
    errors: int = 0  # sources whose fetch or store write failed
    enriched: int = 0  # items taken off the enrich queue
    duration_s: float = 0.0

    def line(self) -> str:
        return (
            f"ingest due={self.due} fetched={self.fetched} new={self.new} "
            f"refreshed={self.refreshed} not_modified={self.not_modified} "
            f"errors={self.errors} enriched={self.enriched} duration={self.duration_s:.1f}s"
        )


class SourceResult(BaseModel):
    """What fetching one source produced: items plus the outcome to record."""

    items: list[Item]
    outcome: FetchOutcome
    not_modified: bool = False


@dataclass(frozen=True)
class FetchContext:
    cfg: Config
    client: httpx.AsyncClient
    now: datetime
    github_token: str | None = field(default=None, repr=False)


# --- fetcher warnings -> source errors ----------------------------------------
#
# Fetchers never raise: on failure they log a warning and return []. To record that in
# sources.last_error, each fetch runs with a per-task bucket that collects warnings
# logged under digest.fetchers. A fetch that returns no items and logged a warning has
# failed; one that still returned items (e.g. one of two endpoints failed) is ok.

_warnings: ContextVar[list[str] | None] = ContextVar("fetch_warnings", default=None)


class _WarningCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)

    def emit(self, record: logging.LogRecord) -> None:
        bucket = _warnings.get()
        if bucket is not None:
            bucket.append(record.getMessage())


@contextmanager
def _collect_fetcher_warnings() -> Iterator[None]:
    logger = logging.getLogger("digest.fetchers")
    handler = _WarningCollector()
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)


def _clip(message: str) -> str:
    message = " ".join(message.split())
    return message if len(message) <= ERROR_MAX_CHARS else message[: ERROR_MAX_CHARS - 1] + "…"


async def _guarded(call: Callable[[], Awaitable[list[Item]]], at: datetime) -> SourceResult:
    bucket: list[str] = []
    token = _warnings.set(bucket)
    try:
        items = await call()
    except Exception as exc:  # a fetcher broke its contract; still only this source fails
        log.exception("fetcher raised unexpectedly")
        return SourceResult(items=[], outcome=_failed(at, f"{type(exc).__name__}: {exc}"))
    finally:
        _warnings.reset(token)
    if not items and bucket:
        return SourceResult(items=[], outcome=_failed(at, bucket[0]))
    return SourceResult(items=items, outcome=FetchOutcome(at=at, ok=True))


def _failed(at: datetime, error: str) -> FetchOutcome:
    return FetchOutcome(at=at, ok=False, error=_clip(error) or "unknown error")


# --- one fetch per source kind ------------------------------------------------


def _api_call(source: Source, ctx: FetchContext) -> Callable[[], Awaitable[list[Item]]] | None:
    client, now, cfg = ctx.client, ctx.now, ctx.cfg
    match source.kind:
        case "hackernews":
            return lambda: hackernews.fetch(cfg, client, now)
        case "lobsters":
            return lambda: lobsters.fetch(cfg, client, now)
        case "github_trending":
            return lambda: github_trending.fetch(cfg, client, now)
        case "hf_papers":
            return lambda: hf_papers.fetch(client, now=now)
        case "lemmy":
            return lambda: lemmy.fetch(client, now=now)
        case "viblo":
            return lambda: viblo.fetch(client, now=now)
        case "producthunt":
            return lambda: producthunt_rss.fetch(client, now=now)
        case "github_search":
            gs = cfg.ingest.github_search
            return lambda: github_search.fetch(
                client,
                now=now,
                token=ctx.github_token,
                days=gs.days,
                max_fork_ratio=gs.max_fork_ratio,
                min_fork_ratio=gs.min_fork_ratio,
                zero_fork_min_stars=gs.zero_fork_min_stars,
            )
    return None


async def _fetch_rss(source: Source, ctx: FetchContext) -> SourceResult:
    result = await rss.fetch_feed(
        ctx.client,
        name=source.name,
        url=source.url,
        base_weight=source.base_weight,
        now=ctx.now,
        etag=source.etag,
        last_modified=source.last_modified,
        max_age=timedelta(days=ctx.cfg.ingest.rss_max_age_days),
    )
    if result.error is not None:
        return SourceResult(items=[], outcome=_failed(ctx.now, result.error))
    outcome = FetchOutcome(
        at=ctx.now, ok=True, etag=result.etag, last_modified=result.last_modified
    )
    return SourceResult(items=result.items, outcome=outcome, not_modified=result.not_modified)


async def _fetch_one(source: Source, ctx: FetchContext) -> SourceResult:
    if source.kind == "rss":
        return await _fetch_rss(source, ctx)
    call = _api_call(source, ctx)
    if call is None:
        return SourceResult(items=[], outcome=_failed(ctx.now, f"no fetcher for {source.kind}"))
    return await _guarded(call, ctx.now)


async def _fetch_devto(
    sources: Sequence[Source], ctx: FetchContext, pause_seconds: float
) -> list[tuple[Source, SourceResult]]:
    """dev.to answers 429 to bursts, so its tag sources are fetched one after another.
    The overall top list rides along with the first one."""
    results = []
    for n, source in enumerate(sources):
        if n and pause_seconds:
            await asyncio.sleep(pause_seconds)
        tag = source.url.rstrip("/").rsplit("/", 1)[-1]
        result = await _guarded(
            lambda tag=tag, first=(n == 0): devto.fetch(
                ctx.client, tags=[tag], now=ctx.now, include_top=first, pause_seconds=pause_seconds
            ),
            ctx.now,
        )
        results.append((source, result))
    return results


async def fetch_sources(
    sources: Sequence[Source], ctx: FetchContext, *, devto_pause_seconds: float = 1.0
) -> list[tuple[Source, SourceResult]]:
    """Fetch every source concurrently: at most `ingest.concurrency` sources in flight,
    and at most `ingest.per_host` per host. Results come back in the input order."""
    overall = asyncio.Semaphore(ctx.cfg.ingest.concurrency)
    hosts: defaultdict[str, asyncio.Semaphore] = defaultdict(
        lambda: asyncio.Semaphore(ctx.cfg.ingest.per_host)
    )

    async def limited(
        host: str, job: Callable[[], Awaitable[list[tuple[Source, SourceResult]]]]
    ) -> list[tuple[Source, SourceResult]]:
        # Host slot first, so a busy host never holds global slots while it waits.
        async with hosts[host], overall:
            return await job()

    async def single(source: Source) -> list[tuple[Source, SourceResult]]:
        return [(source, await _fetch_one(source, ctx))]

    jobs = []
    for source in sources:
        if source.kind != "devto":
            jobs.append(limited(_host(source.url), lambda s=source: single(s)))
    devto_sources = [s for s in sources if s.kind == "devto"]
    if devto_sources:
        pause = devto_pause_seconds
        jobs.append(limited("dev.to", lambda: _fetch_devto(devto_sources, ctx, pause)))
    with _collect_fetcher_warnings():
        done = await asyncio.gather(*jobs)
    by_id = {source.id: result for batch in done for source, result in batch}
    return [(source, by_id[source.id]) for source in sources]


def _host(url: str) -> str:
    return (urlsplit(url).hostname or url).lower()


# --- store writes -------------------------------------------------------------


def store_results(
    store: ContentStore,
    results: Sequence[tuple[Source, SourceResult]],
    now: datetime,
    stats: IngestStats,
) -> None:
    """Upsert each source's items, then record its fetch. One source failing (even in
    the store) is recorded and never stops the others."""
    for source, result in results:
        label = f"{source.kind}:{source.name}"
        try:
            items = _new_items(normalize(result.items), source, now)
            upserted = store.upsert_items(items)
            store.record_fetch(source.id, result.outcome)
        except Exception as exc:
            log.exception("%s: storing items failed", label)
            stats.errors += 1
            try:
                store.record_fetch(source.id, _failed(now, f"store: {type(exc).__name__}"))
            except Exception:
                log.exception("%s: recording the failure failed too", label)
            continue
        stats.fetched += len(items)
        stats.new += len(upserted.new_ids)
        stats.refreshed += upserted.refreshed
        if result.not_modified:
            stats.not_modified += 1
            log.info("%s: not modified", label)
        elif not result.outcome.ok:
            stats.errors += 1
            log.warning("%s: failed: %s", label, result.outcome.error)
        else:
            log.info(
                "%s: %d items, %d new, %d refreshed, %d known from another source",
                label,
                len(items),
                len(upserted.new_ids),
                upserted.refreshed,
                upserted.other_source,
            )


def _new_items(items: Sequence[Item], source: Source, now: datetime) -> list[NewItem]:
    out = []
    for item in items:
        try:
            out.append(NewItem.from_item(item, source.id, now))
        except ValidationError as exc:
            log.warning("%s: skipped invalid item %s: %s", source.name, item.url, exc.errors()[0])
    return out


# --- enrichment ---------------------------------------------------------------


async def enrich_pending(
    store: ContentStore, cfg: Config, client: httpx.AsyncClient, now: datetime
) -> int:
    """Enrich up to `ingest.enrich_per_run` queued items. Every item taken off the queue
    is marked done, even when its page could not be used, so it is never retried."""
    pending = store.pending(Stage.ENRICH, cfg.ingest.enrich_per_run)
    if not pending:
        return 0
    results = await enrich_many(
        client,
        [item.url for item in pending],
        concurrency=cfg.ingest.concurrency,
        per_host=cfg.ingest.per_host,
        timeout_s=cfg.ingest.enrich_timeout_s,
        max_items=len(pending),
        robots=RobotsCache(),
    )
    updates = []
    reasons: Counter[str] = Counter()
    hits: Counter[str] = Counter()
    for item in pending:
        found = results.get(item.url) or Enrichment(skip_reason="not fetched")
        reasons[found.skip_reason or "ok"] += 1
        hits["image"] += found.image_url is not None
        hits["description"] += item.description is None and found.description is not None
        hits["reading_min"] += found.reading_min is not None
        hits["lang"] += found.lang is not None
        updates.append(
            EnrichmentUpdate(
                id=item.id,
                image_url=found.image_url,
                description=found.description,
                reading_min=found.reading_min,
                lang=found.lang,
            )
        )
    saved = store.save_enrichment(updates, now)
    log.info(
        "enrich: %d items; filled image=%d description=%d reading_min=%d lang=%d; outcomes %s",
        len(pending),
        hits["image"],
        hits["description"],
        hits["reading_min"],
        hits["lang"],
        ", ".join(f"{reason}={n}" for reason, n in reasons.most_common()),
    )
    return saved


# --- the run ------------------------------------------------------------------


async def run_ingest(
    store: ContentStore,
    cfg: Config,
    specs: Sequence[SourceSpec],
    client: httpx.AsyncClient,
    *,
    now: datetime,
    github_token: str | None = None,
    devto_pause_seconds: float = 1.0,
) -> IngestStats:
    started = time.monotonic()
    stats = IngestStats()

    synced = store.sync_sources(specs)
    if synced.changed:
        log.info(
            "sources: %d added, %d updated, %d disabled",
            synced.inserted,
            synced.updated,
            synced.disabled,
        )

    due = store.due_sources(now, slack=timedelta(minutes=cfg.ingest.due_slack_minutes))
    stats.due = len(due)
    ctx = FetchContext(cfg=cfg, client=client, now=now, github_token=github_token)
    results = await fetch_sources(due, ctx, devto_pause_seconds=devto_pause_seconds)
    store_results(store, results, now, stats)

    stats.enriched = await enrich_pending(store, cfg, client, now)

    changed = store.recompute_pop_pct(now, timedelta(days=cfg.ingest.pop_window_days))
    log.info("pop_pct: %d items changed", changed)

    stats.duration_s = time.monotonic() - started
    return stats
