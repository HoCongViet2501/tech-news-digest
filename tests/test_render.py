import html
import re
from datetime import UTC, date, datetime
from pathlib import Path

import feedparser
import pytest

from digest.models import Digest, ScoredItem
from digest.render import TELEGRAM_LIMIT, build_site, render_telegram
from snapshot import assert_snapshot

TRICKY = 'Rust & C++ <templates> "quoted" 🚀'
BASE = "https://example.github.io/tech-digest/"


def scored(n: int, **kw) -> ScoredItem:
    defaults = dict(
        id=f"id{n}",
        source="hn",
        title=f"Story {n}",
        url=f"https://example.com/{n}",
        discussion_url=f"https://news.ycombinator.com/item?id={n}",
        score=100 + n,
        comments=n,
        published_at=datetime(2026, 9, 26, 12, n % 60, tzinfo=UTC),
        relevance=5.0,
    )
    return ScoredItem(**(defaults | kw))


def sample_digest(day: date = date(2026, 9, 27)) -> Digest:
    return Digest(
        date=day,
        items=[
            scored(1, title=TRICKY, url="https://example.com/a?x=1&y=2", also_on=["lobsters"]),
            scored(2, source="github", title="owner/repo: A tool", discussion_url=None, score=450),
            scored(3, source="lobsters", title="Plain title", score=42),
        ],
    )


# --- Telegram ------------------------------------------------------------------


def test_telegram_escapes_html_and_keeps_emoji() -> None:
    [msg] = render_telegram(sample_digest(), "vi")

    assert "Rust &amp; C++ &lt;templates&gt;" in msg
    assert "<templates>" not in msg
    assert "🚀" in msg
    assert 'href="https://example.com/a?x=1&amp;y=2"' in msg


def test_telegram_uses_only_supported_tags() -> None:
    [msg] = render_telegram(sample_digest(), "vi")

    assert set(re.findall(r"</?([a-zA-Z]+)", msg)) <= {"b", "i", "a", "code"}


def test_telegram_omits_discussion_link_when_missing() -> None:
    [msg] = render_telegram(sample_digest(), "vi")
    github_block = msg.split("2. ", 1)[1].split("3. ", 1)[0]

    assert "news.ycombinator.com" not in github_block


def test_telegram_splits_long_digest_without_splitting_items() -> None:
    items = [scored(n, title=f"Item-{n:02d} " + "x" * 300) for n in range(1, 31)]
    messages = render_telegram(Digest(date=date(2026, 9, 27), items=items), "vi")

    assert len(messages) > 1
    assert all(len(m) <= TELEGRAM_LIMIT for m in messages)
    joined = "\n".join(messages)
    for n in range(1, 31):
        # each item appears once, and its title and discussion link share a message
        assert joined.count(f"Item-{n:02d} ") == 1
        [holder] = [m for m in messages if f"Item-{n:02d} " in m]
        assert f"item?id={n}" in holder
    order = [int(x) for x in re.findall(r"Item-(\d\d) ", joined)]
    assert order == list(range(1, 31))


def test_telegram_snapshot() -> None:
    assert_snapshot("telegram_vi.html", "\n=====\n".join(render_telegram(sample_digest(), "vi")))


# --- Site ------------------------------------------------------------------------


@pytest.fixture
def site(tmp_path: Path) -> Path:
    digests = [
        sample_digest(date(2026, 9, 27)),
        Digest(date=date(2026, 9, 26), items=[scored(7, title="Yesterday story")]),
        Digest(date=date(2026, 8, 1), items=[scored(8, title="Old story")]),
    ]
    build_site(digests, tmp_path, base_url=BASE, language="vi")
    return tmp_path


def test_site_writes_one_page_per_day_plus_index_and_feed(site: Path) -> None:
    names = sorted(p.name for p in site.iterdir())

    assert names == [
        "2026-08-01.html",
        "2026-09-26.html",
        "2026-09-27.html",
        "feed.xml",
        "index.html",
    ]


def test_index_shows_latest_digest_and_archive(site: Path) -> None:
    html = (site / "index.html").read_text(encoding="utf-8")

    assert "Rust &amp; C++ &lt;templates&gt;" in html
    assert "Yesterday story" not in html
    for day in ("2026-09-27", "2026-09-26", "2026-08-01"):
        assert f'href="{day}.html"' in html


def test_pages_support_mobile_and_dark_mode(site: Path) -> None:
    html = (site / "2026-09-26.html").read_text(encoding="utf-8")

    assert 'name="viewport"' in html
    assert "prefers-color-scheme: dark" in html
    assert "Yesterday story" in html


def test_feed_parses_and_covers_last_30_days(site: Path) -> None:
    feed = feedparser.parse((site / "feed.xml").read_bytes())

    assert not feed.bozo, feed.get("bozo_exception")
    assert feed.version == "rss20"
    titles = [e.title for e in feed.entries]
    # 3 items on 09-27 + 1 on 09-26; 08-01 is older than 30 days
    assert sorted(titles) == sorted(
        [TRICKY, "owner/repo: A tool", "Plain title", "Yesterday story"]
    )
    tricky = next(e for e in feed.entries if e.title == TRICKY)
    assert tricky.link == "https://example.com/a?x=1&y=2"


def test_day_page_snapshot(site: Path) -> None:
    assert_snapshot("day_2026-09-27.html", (site / "2026-09-27.html").read_text(encoding="utf-8"))


def test_feed_snapshot(site: Path) -> None:
    assert_snapshot("feed.xml", (site / "feed.xml").read_text(encoding="utf-8"))


# --- AI fields (phase 2) -----------------------------------------------------------


def ai_digest(**stats) -> Digest:
    return Digest(
        date=date(2026, 9, 27),
        items=[
            scored(
                1,
                relevance=8.0,
                reason="Liên quan Postgres",
                summary="Postgres 19 thêm <async I/O> & nhiều cải tiến.",
                why_it_matters="Bạn dùng Postgres hằng ngày.",
            ),
            scored(2, relevance=7.5, reason="r", summary=None, why_it_matters=None),
            scored(3, relevance=6.0),  # not AI-scored: no reason
        ],
        stats={"ai": "groq", **stats},
    )


def test_telegram_shows_summary_why_and_ai_score() -> None:
    [msg] = render_telegram(ai_digest(), "vi")
    first = msg.split("\n\n")[1]

    assert "Postgres 19 thêm &lt;async I/O&gt; &amp; nhiều cải tiến." in first
    assert "💡 Bạn dùng Postgres hằng ngày." in first
    assert "8/10" in first
    assert "7.5/10" in msg


def test_telegram_hides_score_for_items_without_ai_reason() -> None:
    [msg] = render_telegram(ai_digest(), "vi")
    third = msg.split("\n\n")[3]

    assert "/10" not in third


def test_telegram_notes_when_ai_was_unavailable() -> None:
    digest = sample_digest().model_copy(update={"stats": {"ai": "unavailable"}})

    [msg] = render_telegram(digest, "vi")

    assert "AI không khả dụng hôm nay" in msg.split("\n\n")[0]


def test_telegram_has_no_ai_note_when_ai_worked_or_disabled() -> None:
    assert "AI không khả dụng" not in render_telegram(ai_digest(), "vi")[0]
    assert "AI không khả dụng" not in render_telegram(sample_digest(), "vi")[0]


def test_telegram_ai_snapshot() -> None:
    assert_snapshot("telegram_ai_vi.html", "\n=====\n".join(render_telegram(ai_digest(), "vi")))


def test_site_and_feed_show_summary_and_why(tmp_path: Path) -> None:
    build_site([ai_digest()], tmp_path, base_url=BASE, language="vi")

    page = (tmp_path / "2026-09-27.html").read_text(encoding="utf-8")
    assert "Postgres 19 thêm &lt;async I/O&gt; &amp; nhiều cải tiến." in page
    assert "Bạn dùng Postgres hằng ngày." in page
    assert "8/10" in page
    feed = feedparser.parse((tmp_path / "feed.xml").read_bytes())
    entry = next(e for e in feed.entries if e.title == "Story 1")
    # description is HTML; readers display its unescaped text, so "<async I/O>" survives
    shown = html.unescape(entry.description)
    assert "Postgres 19 thêm <async I/O> & nhiều cải tiến." in shown
    assert "Bạn dùng Postgres hằng ngày." in shown
