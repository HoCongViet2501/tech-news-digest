"""Pure parsing: meta tags, description, reading time, language, transient main text."""

import json

import pytest

from conftest import fixture_bytes
from digest.enrich import Enrichment
from digest.enrich.lang import detect_lang, detect_text, normalize_declared
from digest.enrich.page import clean_text, decode_html, parse_page, truncate


def parse(
    name: str, url: str = "https://example.com/post/1", ctype: str = "text/html"
) -> Enrichment:
    return parse_page(decode_html(fixture_bytes(f"enrich/{name}"), ctype), url)


def test_og_tags_win_over_twitter_and_meta() -> None:
    e = parse("og_tags.html")

    assert e.image_url == "https://cdn.example.com/covers/autovacuum.png"
    assert e.description == "How & why autovacuum falls behind."
    assert e.lang == "en"
    assert e.reading_min == 1
    assert e.main_text and "visibility map" in e.main_text


def test_relative_twitter_image_resolved_against_base_and_non_http_rejected() -> None:
    e = parse("relative_image.html", url="https://blog.example.org/2026/post")

    # og:image is javascript: and is rejected, so twitter:image is used, resolved via <base>.
    assert e.image_url == "https://blog.example.org/static/img/cover.jpg"
    assert e.description == "Meta description only."


def test_no_meta_falls_back_to_main_text() -> None:
    e = parse("no_meta.html")

    assert e.image_url is None
    assert e.description is not None
    assert e.description.startswith("Vacuum notes Postgres keeps every row version")
    assert len(e.description) <= 301 and e.description.endswith("…")
    assert e.lang == "en"  # no declaration at all: heuristic
    assert e.reading_min == 1


def test_reading_time_from_word_count() -> None:
    # 10 distinct paragraphs of 230 words (trafilatura drops repeated paragraphs).
    paras = "".join(f"<p>{f'p{i} ' * 230}</p>" for i in range(10))
    html = f"<html><body><article>{paras}</article></body></html>"

    e = parse_page(html, "https://example.com/a")

    assert e.reading_min == 10


def test_empty_page_has_no_reading_time_or_lang() -> None:
    e = parse_page("<html><head><title>x</title></head><body></body></html>", "https://e.com/")

    assert e.reading_min is None
    assert e.main_text is None
    assert e.lang is None


def test_broken_charset_does_not_fail() -> None:
    # Header names a codec that does not exist, the meta says utf-8, the bytes are cp1252
    # plus junk. Decoding must still produce usable text.
    e = parse("broken_charset.html", ctype="text/html; charset=bogus-8")

    assert e.description is not None
    assert e.description.startswith("Caf")
    assert "and the price." in e.description
    assert e.lang == "en"


@pytest.mark.parametrize(
    ("name", "image_host", "lang", "desc_start"),
    [
        ("real_viblo_vi.html", "images.viblo.asia", "vi", "Hầu hết model Django"),
        ("real_vnexpress_vi.html", "i2-vnexpress.vnecdn.net", "vi", "Microsoft cho biết"),
        ("real_devto_en.html", "media2.dev.to", "en", "When DHH took the stage"),
        ("real_zenn_ja.html", "res.cloudinary.com", "other", "1時間で作るPerlOS"),
    ],
)
def test_real_pages(name: str, image_host: str, lang: str, desc_start: str) -> None:
    e = parse(name)

    assert e.image_url and e.image_url.startswith(f"https://{image_host}/")
    assert e.lang == lang
    assert e.description and e.description.startswith(desc_start)
    assert len(e.description) <= 301
    assert e.reading_min and e.reading_min >= 1


def test_viblo_declares_en_but_text_says_vi() -> None:
    html = fixture_bytes("enrich/real_viblo_vi.html").decode()
    assert '<html amp="" lang="en"' in html

    assert parse("real_viblo_vi.html").lang == "vi"


def test_main_text_is_never_serialized() -> None:
    e = parse("og_tags.html")
    assert e.main_text

    dumped = e.model_dump()
    as_json = e.model_dump_json()

    assert "main_text" not in dumped
    assert "main_text" not in json.loads(as_json)
    assert "visibility map" not in as_json
    assert "visibility map" not in repr(e)
    assert "main_text" not in Enrichment.model_json_schema(mode="serialization")["properties"]


# --- language heuristic ------------------------------------------------------------------

SAMPLES = {
    "Hướng dẫn cài đặt Docker trên Ubuntu cho người mới bắt đầu": "vi",
    "Tối ưu hóa query PostgreSQL với index và EXPLAIN ANALYZE: khi nào nên dùng B-tree": "vi",
    "Show HN: A tiny Rust crate for parsing robots.txt files": "en",
    "Use the flag to enable it: const x = await fetch(url); The function returns a promise.": "en",
    "Le gouvernement a annoncé une nouvelle réforme des retraites qui sera présentée": "other",
    "O governo anunciou uma nova reforma da previdência que será apresentada ao Congresso": "other",
    "Die Bundesregierung hat eine neue Rentenreform angekündigt, die nächste Woche": "other",
    "Guvernul a anunțat o nouă reformă a pensiilor care va fi prezentată în Parlament": "other",
    "このアプリ作ろうと思ったら、ざっくり工数どのくらい？と聞かれて答えてしまった経験": None,
    "このアプリ作ろうと思ったら、ざっくり工数どのくらい？と聞かれて答えてしまった経験" * 3: "other",
    "too short": None,
}


@pytest.mark.parametrize(("text", "expected"), SAMPLES.items())
def test_detect_text(text: str, expected: str | None) -> None:
    assert detect_text(text) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("vi-VN", "vi"), ("en_US", "en"), ("EN", "en"), ("ja", "other"), ("", None), (None, None)],
)
def test_normalize_declared(value: str | None, expected: str | None) -> None:
    assert normalize_declared(value) == expected


def test_declared_lang_breaks_ties_when_text_is_inconclusive() -> None:
    assert detect_lang("short", ["", "vi_VN"]) == "vi"
    assert detect_lang("short", [None, None]) is None
    # Code-heavy text without stopwords is "other" by heuristic; the declaration wins.
    code = "kubectl get pods -n kube-system --output=wide | grep CrashLoopBackOff " * 3
    assert detect_lang(code, ["en"]) == "en"


def test_clean_text_and_truncate() -> None:
    assert clean_text("  a <i>b</i>\n\n c &amp; d ") == "a b c & d"
    assert clean_text("   ") is None
    assert truncate("x" * 400).endswith("…")
    assert truncate("word " * 100, 20) == "word word word word…"


def test_utf8_cut_mid_character_by_size_limit_stays_utf8() -> None:
    body = "<html><body><p>Tiếng Việt có dấu</p></body></html>".encode()
    cut = body[: body.index("ệ".encode()) + 1]  # half of a multi-byte character

    assert decode_html(cut, "text/html").endswith("Tiếng Vi")
    assert "Tiếng" in decode_html(body, None)
