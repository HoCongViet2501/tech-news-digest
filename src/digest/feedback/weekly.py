"""Sunday digest: the week's best items by score and feedback, plus feedback stats."""

from collections import Counter
from datetime import date, timedelta

from pydantic import BaseModel, Field

from digest.models import Digest, ScoredItem, Vote
from digest.state import latest_votes


class ProfileSuggestion(BaseModel):
    change: str  # in output_language
    why: str


class WeeklyItem(ScoredItem):
    vote: str | None = None  # "up" | None; disliked items are never picked


class WeeklyReport(BaseModel):
    start: date
    end: date
    top: list[WeeklyItem]
    items_sent: int
    votes: int
    likes: int
    best_source: str | None  # most liked source this week
    suggestions: list[ProfileSuggestion] = Field(default_factory=list)

    @property
    def like_ratio(self) -> float | None:
        return self.likes / self.votes if self.votes else None


def build_weekly(
    digests: list[Digest], votes: list[Vote], end: date, top_items: int
) -> WeeklyReport:
    start = end - timedelta(days=6)
    items: dict[str, ScoredItem] = {}
    for digest in sorted(digests, key=lambda d: d.date):
        if start <= digest.date <= end:
            for item in digest.items:
                items.setdefault(item.id, item)

    latest = latest_votes(votes)
    week_votes = [latest[i] for i in items if i in latest]
    liked = {v.item_id for v in week_votes if v.vote == "up"}
    disliked = {v.item_id for v in week_votes if v.vote == "down"}

    # Liked first, then AI-scored items; heuristic relevance (pre-AI days, or days
    # the AI was down) is on a different scale, so it only ranks among itself.
    candidates = [i for i in items.values() if i.id not in disliked]
    candidates.sort(
        key=lambda i: (i.id in liked, i.reason is not None, i.relevance, i.score), reverse=True
    )
    top = [
        WeeklyItem(**i.model_dump(), vote="up" if i.id in liked else None)
        for i in candidates[:top_items]
    ]

    likes_by_source = Counter(v.source for v in week_votes if v.vote == "up")
    votes_by_source = Counter(v.source for v in week_votes)
    best = max(
        likes_by_source,
        key=lambda s: (likes_by_source[s], likes_by_source[s] / votes_by_source[s], s),
        default=None,
    )
    return WeeklyReport(
        start=start,
        end=end,
        top=top,
        items_sent=len(items),
        votes=len(week_votes),
        likes=len(liked),
        best_source=best,
    )
