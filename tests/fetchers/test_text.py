from digest.fetchers.text import excerpt


def test_strips_html_and_entities() -> None:
    assert excerpt("<p>Hello&nbsp;<b>world</b> &amp; co</p>") == "Hello world & co"


def test_collapses_whitespace() -> None:
    assert excerpt("  a\n\n  b\tc ") == "a b c"


def test_empty_becomes_none() -> None:
    assert excerpt(None) is None
    assert excerpt("") is None
    assert excerpt("<p> </p>") is None


def test_long_text_is_cut_at_a_word_boundary() -> None:
    text = "word " * 100

    result = excerpt(text, limit=50)

    assert result is not None
    assert len(result) <= 50
    assert result.endswith("word…")


def test_plain_text_that_looks_like_a_url_is_kept() -> None:
    assert excerpt("https://example.com/a?b=1&c=2") == "https://example.com/a?b=1&c=2"
