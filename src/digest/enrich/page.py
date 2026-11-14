"""Pure HTML -> Enrichment parsing (no I/O)."""

import codecs
import logging
import re
from urllib.parse import urljoin, urlsplit

import trafilatura
from bs4 import BeautifulSoup, Tag
from bs4.dammit import EncodingDetector

from digest.enrich.lang import detect_lang
from digest.enrich.model import Enrichment

log = logging.getLogger(__name__)

WORDS_PER_MINUTE = 230
DESCRIPTION_MAX_CHARS = 300
# Language detection only needs a sample; this keeps it cheap on long articles.
LANG_SAMPLE_CHARS = 10_000

_SPACE = re.compile(r"\s+")


def decode_html(body: bytes, content_type: str | None) -> str:
    """Bytes -> text. Tries the header charset, the <meta> charset, UTF-8 and windows-1252
    strictly, in that order; unknown codec names are skipped. If none fits, decodes as the
    first candidate with U+FFFD for bad bytes, so a broken charset never fails the page."""
    names: list[str] = []
    match = re.search(r"charset=[\"']?([\w.:-]+)", content_type or "", re.I)
    if match:
        names.append(match.group(1))
    meta = EncodingDetector.find_declared_encoding(body[:65536], is_html=True)
    if meta:
        names.append(meta)
    names += ["utf-8", "windows-1252"]

    codecs_found: list[str] = []
    for name in names:
        try:
            codec = codecs.lookup(name).name
        except LookupError:
            continue
        if codec not in codecs_found:
            codecs_found.append(codec)
    for codec in codecs_found:
        try:
            # final=False tolerates a multi-byte character cut off by the size limit.
            return codecs.getincrementaldecoder(codec)().decode(body, final=False)
        except UnicodeDecodeError:
            continue
    return body.decode(codecs_found[0], errors="replace")


def clean_text(value: str | None) -> str | None:
    """Strip HTML tags/entities and collapse whitespace."""
    if not value:
        return None
    if "<" in value or "&" in value:
        value = BeautifulSoup(value, "html.parser").get_text(" ")
    value = _SPACE.sub(" ", value).strip()
    return value or None


def truncate(text: str, limit: int = DESCRIPTION_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] if " " in text[:limit] else text[:limit]
    return cut.rstrip(" ,.;:-") + "…"


def _meta(soup: BeautifulSoup, *keys: str) -> str | None:
    """First non-empty content of <meta property|name=key>, trying keys in order."""
    for key in keys:
        for tag in soup.find_all("meta"):
            if not isinstance(tag, Tag):
                continue
            names = {str(tag.get(attr, "")).strip().lower() for attr in ("property", "name")}
            if key in names:
                content = str(tag.get("content") or "").strip()
                if content:
                    return content
    return None


def _absolute_http(candidate: str | None, base: str) -> str | None:
    if not candidate:
        return None
    url = urljoin(base, candidate.strip())
    parts = urlsplit(url)
    if parts.scheme in ("http", "https") and parts.netloc:
        return url
    return None


def _base_url(soup: BeautifulSoup, page_url: str) -> str:
    base = soup.find("base", href=True)
    if isinstance(base, Tag):
        return urljoin(page_url, str(base["href"]).strip())
    return page_url


def extract_main_text(html: str, url: str) -> str | None:
    try:
        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False)
    except Exception as exc:  # trafilatura/lxml can choke on odd markup
        log.info("trafilatura failed on %s: %s", url, exc)
        return None
    if not text or not text.strip():
        return None
    return text.strip()


def parse_page(html: str, url: str) -> Enrichment:
    """Meta tags + main text of one page. `url` is the final URL after redirects."""
    soup = BeautifulSoup(html, "lxml")
    base = _base_url(soup, url)

    image_url = _absolute_http(_meta(soup, "og:image"), base) or _absolute_http(
        _meta(soup, "twitter:image", "twitter:image:src"), base
    )

    main_text = extract_main_text(html, url)
    description = clean_text(_meta(soup, "og:description")) or clean_text(
        _meta(soup, "description")
    )
    if not description and main_text:
        description = clean_text(main_text)
    if description:
        description = truncate(description)

    reading_min = None
    if main_text:
        words = len(main_text.split())
        reading_min = max(1, round(words / WORDS_PER_MINUTE))

    html_tag = soup.find("html")
    html_lang = str(html_tag.get("lang") or "") if isinstance(html_tag, Tag) else None
    title = soup.title.get_text(" ") if soup.title else ""
    sample = main_text or " ".join(filter(None, [title, description]))
    lang = detect_lang(sample[:LANG_SAMPLE_CHARS], [html_lang, _meta(soup, "og:locale")])

    return Enrichment(
        image_url=image_url,
        description=description,
        reading_min=reading_min,
        lang=lang,
        main_text=main_text,
    )
