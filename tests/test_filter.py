from datetime import UTC, datetime

import pytest

from digest.config import Config
from digest.filter import apply_filters, rank
from digest.models import Item

T = datetime(2026, 9, 27, tzinfo=UTC)


def item(
    title: str, source: str = "hn", score: int = 100, id: str | None = None, also_on=()
) -> Item:
    return Item(
        id=id or title,
        source=source,
        title=title,
        url=f"https://example.com/{abs(hash(title))}",
        score=score,
        published_at=T,
        also_on=list(also_on),
    )


@pytest.fixture
def cfg(cfg: Config) -> Config:
    cfg.filters.keywords_include = ["postgres", "llm"]
    cfg.filters.keywords_exclude = ["crypto", "nft"]
    return cfg


def titles(items) -> list[str]:
    return [i.title for i in items]


# --- apply_filters -----------------------------------------------------------


def test_drops_items_already_seen(cfg: Config) -> None:
    items = [item("old", id="a"), item("new", id="b")]

    assert titles(apply_filters(items, cfg, seen={"a"})) == ["new"]


def test_exclude_keyword_is_whole_word_and_case_insensitive(cfg: Config) -> None:
    items = [item("The CRYPTO winter"), item("Cryptography basics"), item("NFTs are back")]

    # "Cryptography" and "NFTs" are different words, so they stay
    assert titles(apply_filters(items, cfg, seen=set())) == ["Cryptography basics", "NFTs are back"]


def test_exclude_wins_over_include(cfg: Config) -> None:
    assert apply_filters([item("Postgres for crypto")], cfg, seen=set()) == []


@pytest.mark.parametrize(
    ("source", "threshold_field", "threshold"),
    [
        ("hn", ("hackernews", "min_points"), 50),
        ("github", ("github_trending", "min_stars_today"), 50),
        ("lobsters", ("lobsters", "min_score"), 10),
        ("reddit", ("reddit", "min_score"), 100),
    ],
)
def test_per_source_threshold_is_inclusive(
    cfg: Config, source: str, threshold_field: tuple[str, str], threshold: int
) -> None:
    section, field = threshold_field
    assert getattr(getattr(cfg.sources, section), field) == threshold
    items = [item("below", source, threshold - 1), item("at", source, threshold)]

    assert titles(apply_filters(items, cfg, seen=set())) == ["at"]


def test_include_keyword_keeps_item_below_threshold(cfg: Config) -> None:
    items = [item("Postgres 19 released", "hn", 12), item("Something else", "hn", 12)]

    assert titles(apply_filters(items, cfg, seen=set())) == ["Postgres 19 released"]


def test_rss_items_have_no_threshold(cfg: Config) -> None:
    assert len(apply_filters([item("feed post", "rss:lwn", 1)], cfg, seen=set())) == 1


# --- rank ----------------------------------------------------------------------


def test_relevance_is_percentile_within_source(cfg: Config) -> None:
    items = [item(f"s{s}", "hn", s) for s in (10, 20, 30, 40)]

    ranked = rank(items, cfg, limit=10)

    # mid-rank percentile: (below + 0.5 * equal) / n * 10
    assert {i.title: i.relevance for i in ranked} == {
        "s40": 8.75,
        "s30": 6.25,
        "s20": 3.75,
        "s10": 1.25,
    }
    assert titles(ranked) == ["s40", "s30", "s20", "s10"]


def test_percentile_is_per_source_not_global(cfg: Config) -> None:
    ranked = rank([item("hn-top", "hn", 60), item("gh-top", "github", 5000)], cfg, limit=10)

    assert [i.relevance for i in ranked] == [5.0, 5.0]


def test_include_keyword_and_also_on_add_bonus(cfg: Config) -> None:
    items = [
        item("plain", "hn", 100),
        item("New LLM eval tool", "hn", 100),
        item("cross-posted", "hn", 100, also_on=["lobsters", "rss:lwn"]),
    ]

    ranked = {i.title: i.relevance for i in rank(items, cfg, limit=10)}

    # all three tie on score -> base 5.0
    assert ranked == {"plain": 5.0, "New LLM eval tool": 8.0, "cross-posted": 7.0}


def test_limit_takes_top_items(cfg: Config) -> None:
    items = [item(f"s{s}", "hn", s) for s in range(1, 21)]

    ranked = rank(items, cfg, limit=3)

    assert titles(ranked) == ["s20", "s19", "s18"]


def test_ties_break_by_raw_score(cfg: Config) -> None:
    # Both are the single item of their source -> same relevance 5.0
    ranked = rank([item("small", "lobsters", 12), item("big", "github", 900)], cfg, limit=10)

    assert titles(ranked) == ["big", "small"]
