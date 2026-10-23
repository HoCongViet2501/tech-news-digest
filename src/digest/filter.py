"""Drop seen/excluded/low-score items, then rank the rest with a heuristic."""

import re
from collections import defaultdict
from collections.abc import Collection, Mapping

from digest.config import Config
from digest.models import Item, ScoredItem

INCLUDE_BONUS = 3.0
ALSO_ON_BONUS = 1.0


def _matches(title: str, keywords: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(kw)}\b", title, re.IGNORECASE) for kw in keywords)


def _threshold(cfg: Config, source: str) -> int | None:
    sources = cfg.sources
    return {
        "hn": sources.hackernews.min_points,
        "github": sources.github_trending.min_stars_today,
        "lobsters": sources.lobsters.min_score,
        "reddit": sources.reddit.min_score,
    }.get(source)  # RSS has no native score, so no threshold


def apply_filters(items: list[Item], cfg: Config, seen: Collection[str]) -> list[Item]:
    kept = []
    for item in items:
        if item.id in seen or _matches(item.title, cfg.filters.keywords_exclude):
            continue
        threshold = _threshold(cfg, item.source)
        below = threshold is not None and item.score < threshold
        if below and not _matches(item.title, cfg.filters.keywords_include):
            continue
        kept.append(item)
    return kept


def _percentiles(items: list[Item]) -> list[float]:
    """Mid-rank percentile (0-10) of each item's score within its own source."""
    by_source: dict[str, list[int]] = defaultdict(list)
    for item in items:
        by_source[item.source].append(item.score)
    result = []
    for item in items:
        scores = by_source[item.source]
        below = sum(1 for s in scores if s < item.score)
        equal = sum(1 for s in scores if s == item.score)
        result.append((below + 0.5 * equal) / len(scores) * 10)
    return result


def rank(
    items: list[Item], cfg: Config, limit: int, weights: Mapping[str, float] | None = None
) -> list[ScoredItem]:
    """Heuristic relevance; can exceed 10 with bonuses. Ties break on raw score.

    `weights` (from feedback) multiply the relevance of each source's items.
    """
    scored = []
    for item, pct in zip(items, _percentiles(items), strict=True):
        relevance = pct + ALSO_ON_BONUS * len(item.also_on)
        if _matches(item.title, cfg.filters.keywords_include):
            relevance += INCLUDE_BONUS
        relevance *= (weights or {}).get(item.source, 1.0)
        scored.append(ScoredItem(**item.model_dump(), relevance=round(relevance, 4)))
    scored.sort(key=lambda s: (s.relevance, s.score), reverse=True)
    return scored[:limit]
