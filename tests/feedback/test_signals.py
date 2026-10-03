from datetime import UTC, date, datetime

from digest.config import FeedbackConfig
from digest.feedback.signals import examples, recent_votes, source_weights
from digest.models import Vote

TODAY = date(2026, 9, 27)


def vote(n: int, kind: str, source: str = "hn", day: int = 26, item: str | None = None) -> Vote:
    return Vote(
        ts=datetime(2026, 9, day, 3, 0, tzinfo=UTC)
        if day > 0
        else datetime(2026, 8, 20, tzinfo=UTC),
        update_id=n,
        item_id=item or f"item{n}",
        vote=kind,
        title=f"Title {n}",
        source=source,
    )


def test_recent_votes_drop_old_ones_and_keep_latest_per_item() -> None:
    votes = [vote(1, "up", item="x"), vote(2, "down", item="x"), vote(3, "up", day=0)]

    recent = recent_votes(votes, TODAY, window_days=30)

    assert [(v.update_id, v.vote) for v in recent] == [(2, "down")]


def test_examples_are_newest_first_and_capped() -> None:
    votes = recent_votes([vote(n, "up" if n % 2 else "down") for n in range(1, 8)], TODAY, 30)

    liked, disliked = examples(votes, limit=2)

    assert liked == ["Title 7", "Title 5"]
    assert disliked == ["Title 6", "Title 4"]


def test_source_weights_follow_like_ratio_within_bounds() -> None:
    votes = [vote(n, "up", "hn") for n in range(1, 9)]
    votes += [vote(n, "down", "github") for n in range(10, 18)]
    votes += [vote(20, "up", "lobsters"), vote(21, "down", "lobsters")]

    weights = source_weights(votes, FeedbackConfig())

    assert weights["hn"] == 1.4  # 9/10 smoothed like ratio
    assert weights["github"] == 0.6
    assert weights["lobsters"] == 1.0
    assert all(0.5 <= w <= 1.5 for w in weights.values())


def test_no_votes_no_weights() -> None:
    assert source_weights([], FeedbackConfig()) == {}
