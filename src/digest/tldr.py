"""Ingest step 9: TL;DR for the Popular pool (docs/PLAN.md §13 Ingestion, §15 LLM budget).

Which items: the top `ingest.tldr.pool_size` of public.popular_feed('popular') that have
no TL;DR yet. The ranking is read from SQL, never recomputed here, and popular_feed
already folds near-duplicate clusters into their canonical item, so a cluster gets one
TL;DR. The stage runs after pop_pct is recomputed, so the pool reflects this run.

Input text: each page is fetched again, transiently, through the enrichment machinery
(robots.txt, project User-Agent, `ingest.enrich_timeout_s`), and the extracted main text
is cut to `input_chars`. When the page is blocked or unusable, the stored description is
used instead. With less than `min_chars` of either, the item is skipped: there is not
enough to summarize honestly. The text is only held in memory for the LLM call.

Queue (summarized_at):
- set when a TL;DR is stored, or when the item is skipped for good (thin text, the model
  answered "" because the content says nothing, or it waited longer than
  `max_age_hours` since first_seen_at, the same age bound as the tagger's LLM step);
- left null when the LLM failed or the per-run cap was reached, so a later run retries;
- items that drop out of the pool without a TL;DR are simply never picked again.

Budget: at most `calls_per_run` batches of `batch_size`. The Gemini free quota is shared
with the daily Telegram digest and the tagger, so a rate-limit (429) answer from any
provider stops the stage for this run, also when it happened earlier in the run (tagger)
or when a fallback provider still answered the batch.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from digest.ai.llm import AIUnavailable, LLMChain
from digest.ai.tldr import TldrInput, llm_tldr_batch
from digest.config import Config
from digest.enrich import Enrichment, RobotsCache, enrich_many
from digest.enrich.page import truncate
from digest.store import ContentStore, Stage, StoredItem

log = logging.getLogger(__name__)


@dataclass
class TldrStats:
    stored: int = 0  # TL;DRs written
    skipped: int = 0  # marked summarized without one (thin text, empty answer, aged out)
    calls: int = 0  # LLM batches sent
    waiting: int = 0  # pool items left for a later run (cap, LLM failure, quota)


def item_content(item: StoredItem, page: Enrichment | None, input_chars: int) -> tuple[str, bool]:
    """The longer of the page's main text and the stored description, cut to
    `input_chars`, and whether it is the page text. Empty when there is neither."""
    main = " ".join(((page.main_text if page else None) or "").split())
    description = " ".join((item.description or "").split())
    if main and len(main) >= len(description):
        return truncate(main, input_chars), True
    return (truncate(description, input_chars) if description else ""), False


async def tldr_pending(
    store: ContentStore,
    cfg: Config,
    client: httpx.AsyncClient,
    chain: LLMChain | None,
    *,
    now: datetime,
) -> TldrStats:
    tc = cfg.ingest.tldr
    stats = TldrStats()
    if chain is None or tc.calls_per_run == 0 or tc.pool_size == 0:
        return stats
    candidates = store.tldr_candidates(now, tc.pool_size)
    if not candidates:
        return stats
    if chain.rate_limited:
        stats.waiting = len(candidates)
        log.warning(
            "tldr: skipped this run, rate limited earlier by %s; %d items wait",
            ", ".join(sorted(chain.rate_limited)),
            len(candidates),
        )
        return stats

    cutoff = now - timedelta(hours=tc.max_age_hours)
    expired = [i.id for i in candidates if i.first_seen_at < cutoff]
    fresh = [i for i in candidates if i.first_seen_at >= cutoff]
    if expired:
        stats.skipped += store.mark_done(Stage.SUMMARIZE, expired, now)

    todo = fresh[: tc.calls_per_run * tc.batch_size]
    pages = await enrich_many(
        client,
        [i.url for i in todo],
        concurrency=cfg.ingest.concurrency,
        per_host=cfg.ingest.per_host,
        timeout_s=cfg.ingest.enrich_timeout_s,
        max_items=len(todo),
        robots=RobotsCache(),
    )
    inputs: list[TldrInput] = []
    thin: list[str] = []
    from_page = 0
    for item in todo:
        content, is_page = item_content(item, pages.get(item.url), tc.input_chars)
        if len(content) < tc.min_chars:
            thin.append(item.id)
            continue
        from_page += is_page
        inputs.append(TldrInput(id=item.id, title=item.title, content=content))
    if thin:
        stats.skipped += store.mark_done(Stage.SUMMARIZE, thin, now)
    del pages  # the article text is never kept beyond this call
    done = len(thin)

    for start in range(0, len(inputs), tc.batch_size):
        batch = inputs[start : start + tc.batch_size]
        stats.calls += 1
        try:
            answers = llm_tldr_batch(
                chain, batch, output_language=cfg.ai.output_language, max_chars=tc.max_chars
            )
        except AIUnavailable:
            log.warning("tldr: every LLM provider failed; stopping for this run")
            break
        written = {i: t for i, t in answers.items() if t}
        empty = [i for i, t in answers.items() if not t]
        stats.stored += store.save_tldrs(written, now)
        if empty:
            stats.skipped += store.mark_done(Stage.SUMMARIZE, empty, now)
        done += len(written) + len(empty)  # ids the model left out wait for a later run
        if chain.rate_limited:
            log.warning(
                "tldr: rate limited by %s; stopping for this run",
                ", ".join(sorted(chain.rate_limited)),
            )
            break

    stats.waiting = len(fresh) - done
    log.info(
        "tldr: pool %d without one; %d stored, %d skipped (%d aged out, %d thin), "
        "%d calls, %d from page text, %d waiting",
        len(candidates),
        stats.stored,
        stats.skipped,
        len(expired),
        len(thin),
        stats.calls,
        from_page,
        stats.waiting,
    )
    return stats
