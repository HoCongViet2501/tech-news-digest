from datetime import UTC, datetime

import pytest

from digest.models import Item
from digest.normalize import canonical_url, normalize

T = datetime(2026, 9, 27, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        (
            "https://WWW.Example.com/Path/?utm_source=x&id=3&ref=hn&fbclid=abc#frag",
            "https://example.com/Path?id=3",
        ),
        ("https://example.com/", "https://example.com"),
        ("https://example.com/a/b/", "https://example.com/a/b"),
        ("https://example.com/x?b=2&a=1", "https://example.com/x?b=2&a=1"),
        ("https://example.com/x?utm_medium=rss&utm_campaign=y", "https://example.com/x"),
        ("https://news.ycombinator.com/item?id=1", "https://news.ycombinator.com/item?id=1"),
        ("http://Example.com:8080/x/", "http://example.com:8080/x"),
        ("https://example.com/x?referrer=a", "https://example.com/x?referrer=a"),
        ("https://wwwhat.com/x", "https://wwwhat.com/x"),
    ],
)
def test_canonical_url(raw: str, want: str) -> None:
    assert canonical_url(raw) == want


def item(source: str, url: str, score: int) -> Item:
    return Item(
        id="raw", source=source, title=f"{source} title", url=url, score=score, published_at=T
    )


def test_items_get_canonical_url_and_id() -> None:
    [out] = normalize([item("hn", "https://www.example.com/post/?utm_source=hn", 10)])

    assert out.url == "https://example.com/post"
    # sha1("https://example.com/post"), computed independently with hashlib
    assert out.id == "833d03178109cdfcbcf8d6415751cc78bfe04c52"


def test_duplicate_keeps_highest_score_and_records_other_sources() -> None:
    out = normalize(
        [
            item("lobsters", "https://example.com/post", 30),
            item("hn", "https://www.example.com/post/", 120),
            item("rss:lwn", "https://example.com/post?utm_source=rss", 60),
        ]
    )

    assert len(out) == 1
    assert out[0].source == "hn"
    assert out[0].score == 120
    assert out[0].also_on == ["lobsters", "rss:lwn"]


def test_same_source_duplicate_is_merged_without_also_on() -> None:
    out = normalize(
        [
            item("github", "https://github.com/a/b", 50),
            item("github", "https://github.com/a/b", 50),
        ]
    )

    assert len(out) == 1
    assert out[0].also_on == []


def test_order_follows_first_occurrence() -> None:
    out = normalize(
        [
            item("hn", "https://a.example/1", 5),
            item("hn", "https://b.example/2", 50),
            item("lobsters", "https://a.example/1", 90),
        ]
    )

    assert [o.url for o in out] == ["https://a.example/1", "https://b.example/2"]
    assert out[0].source == "lobsters"
    assert out[0].also_on == ["hn"]


def test_empty_input() -> None:
    assert normalize([]) == []
