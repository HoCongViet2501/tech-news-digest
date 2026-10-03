"""Turn recent votes into scoring signals: prompt examples and per-source weights."""

from collections import Counter
from collections.abc import Iterable
from datetime import date, timedelta

from digest.config import FeedbackConfig
from digest.models import Vote
from digest.state import latest_votes


def recent_votes(votes: Iterable[Vote], today: date, window_days: int) -> list[Vote]:
    """Latest vote per item collected in the last `window_days`, newest first."""
    cutoff = today - timedelta(days=window_days)
    latest = latest_votes(votes).values()
    recent = [v for v in latest if v.ts.date() > cutoff]
    return sorted(recent, key=lambda v: v.update_id, reverse=True)


def examples(votes: list[Vote], limit: int) -> tuple[list[str], list[str]]:
    """Up to `limit` liked and `limit` disliked titles, newest first."""
    liked = [v.title for v in votes if v.vote == "up"][:limit]
    disliked = [v.title for v in votes if v.vote == "down"][:limit]
    return liked, disliked


def source_weights(votes: list[Vote], cfg: FeedbackConfig) -> dict[str, float]:
    """Weight per source from its like ratio, smoothed so a single vote moves it little.

    No votes -> ratio 0.5 -> the midpoint (1.0 with the default 0.5-1.5 range).
    """
    up = Counter(v.source for v in votes if v.vote == "up")
    total = Counter(v.source for v in votes)
    span = cfg.source_weight_max - cfg.source_weight_min
    return {
        source: round(cfg.source_weight_min + span * (up[source] + 1) / (n + 2), 4)
        for source, n in total.items()
    }
