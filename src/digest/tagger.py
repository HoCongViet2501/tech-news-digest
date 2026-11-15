"""Four-step tagger (docs/PLAN.md §15 Tagging), stopping as soon as a tag is confident.

1. Native tags from the source -> taxonomy aliases (`native`, 0.95).
2. Keyword regexes on title + description (`rule`, 0.9 in the title; 0.7 when only the
   description matches, since that is often a passing mention). Once any tag is
   confident, the tentative ones are dropped.
3. Only when 1-2 found nothing confident: cosine between the item embedding and the
   descriptions of `embed: true` tags (`embed`, mapped into 0.6-0.75). Language tags never
   come from this step.
4. Only when there is still no confident tag and the item looks like tech at all (its
   best cosine against any tag description reaches `llm_min_similarity`): the LLM picks
   from the taxonomy and scores quality, in batches. Its answer replaces the tentative
   (non-confident) tags from steps 2-3; if it fails, those stay.

Parent tags are not added: a child implies its parent at read time (tags.parent).
Storing ancestors too would duplicate the hierarchy in item_tags, go stale when the YAML
moves a tag, and blur which tag a step actually found.

Retry bound for step 4: an item waiting for the LLM keeps tagged_at null (its step 1-3
tags are already stored). Every run re-tags it and retries the LLM, until the LLM
answers or the item is older than `llm_max_age_hours`; then it is marked tagged with
the tags it has. Age needs no extra column and also caps how often an item can burn
quota (24 h = 8 runs); a day-old item has lost most of its feed value anyway.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from digest.ai.llm import AIUnavailable, LLMChain
from digest.ai.tagger import LLMTagItem, llm_tag_batch
from digest.config import TaggingConfig
from digest.embedding import Embedder
from digest.store import ContentStore, ItemTag, Stage, StoredItem, TagMethod, TagUpdate
from digest.taxonomy import LANGUAGE_ROOT, Taxonomy

log = logging.getLogger(__name__)

TAG_STEPS = frozenset({TagMethod.NATIVE, TagMethod.RULE, TagMethod.EMBED})
# The LLM only runs when no tag is confident, so every native or title-rule tag is
# absent then; what it replaces are the tentative guesses (description-only keyword
# hits and embedding tags).
LLM_STEP = frozenset({TagMethod.RULE, TagMethod.EMBED, TagMethod.LLM})


def tag_text(tag_name: str, description: str) -> str:
    return f"{tag_name}. {description}"


@dataclass(frozen=True)
class TagVectors:
    """Unit vectors of every tag description, computed once per run (65 short texts)."""

    slugs: list[str]
    vectors: np.ndarray
    embed_mask: np.ndarray  # rows usable by step 3

    @classmethod
    def build(cls, taxonomy: Taxonomy, embedder: Embedder) -> "TagVectors":
        tags = taxonomy.tags
        vectors = embedder.embed([tag_text(t.name, t.description) for t in tags])
        mask = np.array([t.embed and LANGUAGE_ROOT not in taxonomy.ancestors(t.slug) for t in tags])
        return cls(slugs=[t.slug for t in tags], vectors=vectors, embed_mask=mask)


@dataclass
class Decision:
    item: StoredItem
    tags: list[ItemTag]
    best_similarity: float  # best cosine against any tag description; -1 without a vector
    needs_llm: bool = False


def native_tags(item: StoredItem, taxonomy: Taxonomy, confidence: float) -> list[ItemTag]:
    found = dict.fromkeys(
        taxonomy.alias_map[t] for t in item.native_tags if t in taxonomy.alias_map
    )
    return [ItemTag(tag=s, confidence=confidence, method=TagMethod.NATIVE) for s in found]


def rule_tags(
    item: StoredItem, taxonomy: Taxonomy, title_confidence: float, description_confidence: float
) -> list[ItemTag]:
    """Keyword hits; a hit only in the description gets the lower confidence."""
    out: dict[str, float] = {}
    for slug, rx in taxonomy.keyword_patterns:
        if rx.search(item.title):
            out[slug] = title_confidence
        elif item.description and rx.search(item.description):
            out[slug] = max(out.get(slug, 0.0), description_confidence)
    return [ItemTag(tag=s, confidence=c, method=TagMethod.RULE) for s, c in out.items()]


def embed_tags(
    similarities: np.ndarray, tag_vectors: TagVectors, cfg: TaggingConfig
) -> list[ItemTag]:
    picked: list[tuple[float, str]] = []
    for row in np.flatnonzero(tag_vectors.embed_mask):
        slug = tag_vectors.slugs[row]
        sim = float(similarities[row])
        threshold = cfg.embed_thresholds.get(slug, cfg.embed_threshold)
        if sim >= threshold:
            picked.append((sim, slug))
    picked.sort(reverse=True)
    out = []
    for sim, slug in picked[: cfg.embed_max_tags]:
        threshold = cfg.embed_thresholds.get(slug, cfg.embed_threshold)
        span = max(1e-6, 1 - threshold)
        lo, hi = cfg.embed_confidence_min, cfg.embed_confidence_max
        confidence = lo + (hi - lo) * min(1.0, (sim - threshold) / span)
        out.append(ItemTag(tag=slug, confidence=round(confidence, 3), method=TagMethod.EMBED))
    return out


def _merge(*groups: list[ItemTag]) -> list[ItemTag]:
    best: dict[str, ItemTag] = {}
    for group in groups:
        for tag in group:
            if tag.tag not in best or tag.confidence > best[tag.tag].confidence:
                best[tag.tag] = tag
    return sorted(best.values(), key=lambda t: (-t.confidence, t.tag))


def decide(
    item: StoredItem,
    vector: Sequence[float] | None,
    taxonomy: Taxonomy,
    tag_vectors: TagVectors,
    cfg: TaggingConfig,
) -> Decision:
    """Steps 1-3 for one item; whether it would still benefit from the LLM."""
    found = _merge(
        native_tags(item, taxonomy, cfg.native_confidence),
        rule_tags(item, taxonomy, cfg.rule_confidence, cfg.rule_description_confidence),
    )
    best = -1.0
    confident = any(t.confidence >= cfg.confident for t in found)
    if confident:
        # Stop when confident: tentative evidence (a keyword only in the description)
        # next to a confident tag was wrong about half the time on real items.
        found = [t for t in found if t.confidence >= cfg.confident]
    if vector is not None:
        similarities = tag_vectors.vectors @ np.asarray(vector, dtype=np.float32)
        best = float(similarities.max())
        if not confident:
            found = _merge(found, embed_tags(similarities, tag_vectors, cfg))
    return Decision(
        item=item,
        tags=found,
        best_similarity=best,
        needs_llm=not confident and best >= cfg.llm_min_similarity and item.quality is None,
    )


@dataclass
class TagStats:
    tagged: int = 0  # items marked tagged this run
    llm_calls: int = 0
    llm_items: int = 0  # items the LLM answered for
    llm_waiting: int = 0  # left for the next run (cap reached or providers failed)
    by_method: dict[str, int] = field(default_factory=dict)


def _llm_input(item: StoredItem, source_names: dict[int, str], chars: int) -> LLMTagItem:
    return LLMTagItem(
        id=item.id,
        title=item.title,
        description=(item.description or "")[:chars] or None,
        source=source_names.get(item.source_id, ""),
        native_tags=item.native_tags[:10],
        url=item.url,
    )


def tag_pending(
    store: ContentStore,
    taxonomy: Taxonomy,
    embedder: Embedder,
    cfg: TaggingConfig,
    *,
    now: datetime,
    chain: LLMChain | None,
    llm_calls: int,
    source_names: dict[int, str],
) -> TagStats:
    """Run steps 1-3 on the tag queue (embedded items only), then step 4 within the cap."""
    stats = TagStats()
    pending = store.pending(Stage.TAG, cfg.per_run, after=Stage.EMBED)
    if not pending:
        return stats
    unknown = sorted(set(cfg.embed_thresholds) - set(taxonomy.by_slug))
    if unknown:
        log.warning("tag: embed_thresholds names unknown tags: %s", ", ".join(unknown))
    vectors = store.get_embeddings([i.id for i in pending])
    tag_vectors = TagVectors.build(taxonomy, embedder)
    cutoff = now - timedelta(hours=cfg.llm_max_age_hours)
    decisions = [decide(i, vectors.get(i.id), taxonomy, tag_vectors, cfg) for i in pending]
    queue = [
        d for d in decisions if d.needs_llm and chain is not None and d.item.first_seen_at >= cutoff
    ]
    waiting = {d.item.id for d in queue}
    store.save_tags(
        [
            TagUpdate(id=d.item.id, methods=TAG_STEPS, tags=d.tags, done=d.item.id not in waiting)
            for d in decisions
        ],
        now,
    )
    stats.tagged = len(decisions) - len(waiting)

    if queue and chain is not None:
        batch_size = cfg.llm_batch_size
        for start in range(0, len(queue), batch_size):
            if stats.llm_calls >= llm_calls:
                break
            batch = queue[start : start + batch_size]
            stats.llm_calls += 1
            try:
                answers = llm_tag_batch(
                    chain,
                    taxonomy,
                    [_llm_input(d.item, source_names, cfg.llm_description_chars) for d in batch],
                )
            except AIUnavailable:
                log.warning("tag: every LLM provider failed; %d items wait", len(queue) - start)
                break
            updates = []
            for d in batch:
                answer = answers.get(d.item.id)
                if answer is None:  # provider answered but skipped it: keep steps 1-3
                    updates.append(TagUpdate(id=d.item.id, methods=frozenset(), done=True))
                    continue
                tags = [
                    ItemTag(tag=slug, confidence=cfg.llm_confidence, method=TagMethod.LLM)
                    for slug in answer.tags
                ]
                updates.append(
                    TagUpdate(
                        id=d.item.id, methods=LLM_STEP, tags=tags, quality=answer.quality, done=True
                    )
                )
                stats.llm_items += 1
            store.save_tags(updates, now)
            stats.tagged += len(batch)
            waiting -= {d.item.id for d in batch}
    stats.llm_waiting = len(waiting)
    rows = store.get_item_tags([d.item.id for d in decisions])
    for row in rows:
        stats.by_method[row.method] = stats.by_method.get(row.method, 0) + 1
    log.info(
        "tag: %d items, %d done; llm %d calls, %d answered, %d waiting; tags by method %s",
        len(decisions),
        stats.tagged,
        stats.llm_calls,
        stats.llm_items,
        stats.llm_waiting,
        ", ".join(f"{m}={n}" for m, n in sorted(stats.by_method.items())) or "none",
    )
    return stats
