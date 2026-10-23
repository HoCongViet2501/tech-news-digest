import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml

from digest.__main__ import main
from digest.feedback.weekly import WeeklyReport, build_weekly
from digest.models import Digest, ScoredItem, Vote
from digest.render import render_weekly
from digest.state import append_votes, save_digest

WORKFLOWS = Path(__file__).parents[2] / ".github" / "workflows"
REPO_CONFIG = Path(__file__).parents[2] / "config.yaml"
END = date(2026, 9, 27)  # a Sunday


def item(n: int, relevance: float, source: str = "hn", ai: bool = True) -> ScoredItem:
    return ScoredItem(
        id=f"{n:08d}" + "0" * 32,
        source=source,
        title=f"Story {n} & <co>",
        url=f"https://example.com/{n}",
        score=100 + n,
        published_at=datetime(2026, 9, 20, tzinfo=UTC),
        relevance=relevance,
        reason="r" if ai else None,
    )


def vote(n: int, kind: str, source: str = "hn", update_id: int | None = None) -> Vote:
    return Vote(
        ts=datetime(2026, 9, 26, tzinfo=UTC),
        update_id=update_id or n,
        item_id=f"{n:08d}" + "0" * 32,
        vote=kind,
        title=f"Story {n}",
        source=source,
    )


DIGESTS = [
    Digest(date=date(2026, 9, 20), items=[item(1, 10.0)]),  # previous week, ignored
    Digest(
        date=date(2026, 9, 21),
        items=[item(2, 9.0), item(3, 5.0, "github"), item(4, 14.0, ai=False)],
    ),
    Digest(date=date(2026, 9, 27), items=[item(5, 8.0, "lobsters"), item(6, 7.0), item(7, 6.0)]),
]


def test_week_covers_seven_days_ending_on_the_date() -> None:
    report = build_weekly(DIGESTS, [], END, top_items=5)

    assert (report.start, report.end) == (date(2026, 9, 21), END)
    assert report.items_sent == 6
    assert report.votes == 0
    assert report.like_ratio is None
    assert report.best_source is None


def test_liked_first_disliked_never_heuristic_last() -> None:
    votes = [vote(3, "up", "github"), vote(2, "down"), vote(6, "up"), vote(7, "down", update_id=9)]

    report = build_weekly(DIGESTS, votes, END, top_items=5)

    assert [i.id[:8] for i in report.top] == ["00000006", "00000003", "00000005", "00000004"]
    assert [i.vote for i in report.top] == ["up", "up", None, None]
    assert report.votes == 4
    assert report.likes == 2
    assert report.like_ratio == 0.5


def test_changed_vote_counts_once_and_best_source_is_most_liked() -> None:
    votes = [
        vote(5, "down", "lobsters", 1),
        vote(5, "up", "lobsters", 2),
        vote(6, "up"),
        vote(7, "up"),
    ]

    report = build_weekly(DIGESTS, votes, END, top_items=3)

    assert report.votes == 3
    assert report.best_source == "hn"


def test_render_weekly_escapes_and_shows_stats() -> None:
    report = build_weekly(DIGESTS, [vote(6, "up"), vote(2, "down")], END, top_items=5)

    text = render_weekly(report, "vi")

    assert text.startswith("<b>📅 Tổng kết tuần · 2026-09-21 → 2026-09-27</b>")
    assert "6 tin · 2 lượt bình chọn · 50% thích · nguồn hữu ích nhất: hn" in text
    assert "Story 6 &amp; &lt;co&gt;</a> 👍" in text
    assert set(re.findall(r"</?([a-zA-Z]+)", text)) <= {"b", "i", "a", "code"}


def test_empty_week_says_so() -> None:
    text = render_weekly(
        WeeklyReport(start=END, end=END, top=[], items_sent=0, votes=0, likes=0, best_source=None),
        "vi",
    )

    assert "Tuần này chưa có bản tin nào." in text


def test_weekly_cli_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "data"
    for digest in DIGESTS:
        save_digest(data / "digests", digest)
    append_votes(data / "feedback.jsonl", [vote(6, "up")])

    code = main(
        [
            "weekly",
            "--dry-run",
            "--date",
            "2026-09-27",
            "--config",
            str(REPO_CONFIG),
            "--data-dir",
            str(data),
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("<b>📅 Tổng kết tuần")
    assert '1. <a href="https://example.com/6">' in out


def test_weekly_runs_on_sunday_after_the_daily_digest() -> None:
    daily = yaml.safe_load((WORKFLOWS / "daily.yml").read_text())
    weekly = yaml.safe_load((WORKFLOWS / "weekly.yml").read_text())
    feedback = yaml.safe_load((WORKFLOWS / "feedback.yml").read_text())

    # PyYAML reads the "on" key as True
    assert daily[True]["schedule"] == [{"cron": "0 0 * * *"}]
    assert weekly[True]["schedule"] == [{"cron": "0 1 * * 0"}]
    assert feedback[True]["schedule"] == [{"cron": "0 */3 * * *"}]
    groups = {w["concurrency"]["group"] for w in (daily, weekly, feedback)}
    assert groups == {"digest-state"}
