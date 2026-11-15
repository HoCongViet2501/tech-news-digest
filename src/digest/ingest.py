"""`python -m digest ingest`: one content-store run (docs/PLAN.md §13 Ingestion).

In order: sync sources.yaml, `ranking:` and taxonomy.yaml; prune old per-user activity
(impressions, feed snapshots; `retention:`); fetch the due sources; normalize and
upsert (items keep their source's native tags, and every (item, source) pair gets an
item_mentions row); enrich; embed (fastembed, title + description); embed pending user
profiles (free text + followed-tags centroid -> profiles.user_vec, digest.profiles,
capped by `ingest.profiles.per_run`); cluster near-duplicates over 72 h; tag (native ->
rule -> embedding, then the LLM fallback that also scores quality, capped by
`ingest.llm_calls_per_run`); recompute pop_pct; TL;DR for the Popular pool
(digest.tldr, capped by `ingest.tldr.calls_per_run`); record source health.

Every step is resumable: items are upserted by id, record_fetch runs only after a
source's items are stored (so a crash leaves the source due), and enrichment, embedding,
tagging, TL;DR and profiles work off the `enriched_at` / `embedded_at` / `tagged_at` /
`summarized_at` / `profiles.profile_embedded_at` queues (embedding and tagging only
take items whose previous stage is done), so whatever a killed run left undone is
picked up by the next. Clustering re-reads the whole window each run and only writes
what changed.
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

from digest.ai.llm import LLMChain
from digest.cluster import cluster_recent
from digest.config import Config
from digest.embedding import Embedder, item_text
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
from digest.profiles import embed_profiles
from digest.store import ContentStore, EnrichmentUpdate, FetchOutcome, NewItem, Source, Stage
from digest.store.models import SourceSpec
from digest.tagger import tag_pending
from digest.taxonomy import Taxonomy
from digest.tldr import tldr_pending

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
    embedded: int = 0
    profiles: int = 0  # user profiles embedded
    clustered: int = 0  # near-duplicate groups created or extended
    tagged: int = 0  # items taken off the tag queue (done)
    llm_calls: int = 0  # LLM tag + quality batches
    llm_waiting: int = 0  # items left for the LLM in a later run
    tldr: int = 0  # TL;DRs stored
    tldr_calls: int = 0  # TL;DR LLM batches
    tldr_skipped: int = 0  # pool items marked summarized without a TL;DR
    tldr_waiting: int = 0  # pool items left for a later run
    duration_s: float = 0.0

    def line(self) -> str:
        return (
            f"ingest due={self.due} fetched={self.fetched} new={self.new} "
            f"refreshed={self.refreshed} not_modified={self.not_modified} "
            f"errors={self.errors} enriched={self.enriched} embedded={self.embedded} "
            f"profiles={self.profiles} "
            f"clustered={self.clustered} tagged={self.tagged} llm_calls={self.llm_calls} "
            f"llm_waiting={self.llm_waiting} tldr={self.tldr} tldr_calls={self.tldr_calls} "
            f"tldr_skipped={self.tldr_skipped} tldr_waiting={self.tldr_waiting} "
            f"duration={self.duration_s:.1f}s"
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


# --- embeddings ---------------------------------------------------------------

EMBED_CHUNK = 256  # items written per round trip, so a killed run keeps what it embedded


def embed_pending(store: ContentStore, embedder: Embedder, cfg: Config, now: datetime) -> int:
    """Embed up to `ingest.embed.per_run` enriched items from title + description."""
    ec = cfg.ingest.embed
    pending = store.pending(Stage.EMBED, ec.per_run, after=Stage.ENRICH)
    saved = 0
    started = time.monotonic()
    for start in range(0, len(pending), EMBED_CHUNK):
        chunk = pending[start : start + EMBED_CHUNK]
        vectors = embedder.embed([item_text(i.title, i.description, ec.max_chars) for i in chunk])
        saved += store.save_embeddings(
            {item.id: vector.tolist() for item, vector in zip(chunk, vectors, strict=True)}, now
        )
    if pending:
        log.info(
            "embed: %d items with %s in %.1fs", saved, embedder.name, time.monotonic() - started
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
    taxonomy: Taxonomy | None = None,
    embedder: Embedder | None = None,
    chain: LLMChain | None = None,
) -> IngestStats:
    """One run. Without an embedder the embed, profile, cluster and tag stages are skipped;
    without a taxonomy, tagging is; without an LLM chain, tagging stops after step 3 and
    TL;DR is skipped."""
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
    if store.sync_ranking(cfg.ranking):
        log.info("ranking config updated")
    pruned = store.prune_activity(
        impressions_before=now - timedelta(days=cfg.retention.impressions_days),
        snapshots_before=now - timedelta(days=cfg.retention.feed_snapshots_days),
    )
    if pruned.impressions or pruned.feed_snapshots:
        log.info(
            "retention: deleted %d impressions, %d feed snapshots",
            pruned.impressions,
            pruned.feed_snapshots,
        )
    if taxonomy is not None:
        tags = store.sync_tags(taxonomy.specs())
        if tags.changed:
            log.info(
                "tags: %d added, %d updated, %d retired", tags.inserted, tags.updated, tags.disabled
            )

    due = store.due_sources(now, slack=timedelta(minutes=cfg.ingest.due_slack_minutes))
    stats.due = len(due)
    ctx = FetchContext(cfg=cfg, client=client, now=now, github_token=github_token)
    results = await fetch_sources(due, ctx, devto_pause_seconds=devto_pause_seconds)
    store_results(store, results, now, stats)

    stats.enriched = await enrich_pending(store, cfg, client, now)

    if embedder is not None:
        stats.embedded = embed_pending(store, embedder, cfg, now)
        stats.profiles = embed_profiles(store, embedder, cfg, now)
        stats.clustered = cluster_recent(store, embedder, cfg.ingest.cluster, now=now)
        if taxonomy is not None:
            tagged = tag_pending(
                store,
                taxonomy,
                embedder,
                cfg.ingest.tagging,
                now=now,
                chain=chain,
                llm_calls=cfg.ingest.llm_calls_per_run,
                source_names={s.id: s.name for s in store.list_sources()},
            )
            stats.tagged, stats.llm_calls = tagged.tagged, tagged.llm_calls
            stats.llm_waiting = tagged.llm_waiting

    changed = store.recompute_pop_pct(now, timedelta(days=cfg.ingest.pop_window_days))
    log.info("pop_pct: %d items changed", changed)

    # After pop_pct, so the Popular pool reflects this run's scores.
    tldr = await tldr_pending(store, cfg, client, chain, now=now)
    stats.tldr, stats.tldr_calls = tldr.stored, tldr.calls
    stats.tldr_skipped, stats.tldr_waiting = tldr.skipped, tldr.waiting

    stats.duration_s = time.monotonic() - started
    return stats
