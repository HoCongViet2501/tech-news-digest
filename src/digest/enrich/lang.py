"""Language detection without extra dependencies: vi, en or other.

Declared languages are unreliable (Viblo serves Vietnamese articles with `<html lang="en">`),
so the text decides when it is conclusive and the declaration only breaks ties.
"""

import re
import unicodedata

from digest.enrich.model import Lang

# Letters with diacritics that only (or overwhelmingly) occur in Vietnamese: horn, dot below,
# hook above and đ. Plain acute/grave/circumflex are shared with French, Portuguese, ...
_VI_MARKS = {"\N{COMBINING HORN}", "\N{COMBINING DOT BELOW}", "\N{COMBINING HOOK ABOVE}"}
_VI_DIACRITIC_MARKS = _VI_MARKS | {
    "\N{COMBINING GRAVE ACCENT}",
    "\N{COMBINING ACUTE ACCENT}",
    "\N{COMBINING TILDE}",
    "\N{COMBINING CIRCUMFLEX ACCENT}",
    "\N{COMBINING BREVE}",
}

_EN_STOPWORDS_TEXT = (
    "the and of to a in is that for it with on as are this be was by you not or from at "
    "have an but can we if they which your will has more their what all about when there "
    "use how one so do these our also into than then its"
)
_EN_STOPWORDS = frozenset(_EN_STOPWORDS_TEXT.split())
_WORD = re.compile(r"[^\W\d_]+")

MIN_LETTERS = 40
VI_RATIO = 0.08  # share of letters carrying a Vietnamese diacritic (typically 0.2-0.35)
EN_ASCII_RATIO = 0.95
EN_STOPWORD_RATIO = 0.12  # share of words that are common English stopwords (~0.3 in prose)


def normalize_declared(value: str | None) -> Lang | None:
    """`vi-VN`, `en_US`, `ja` -> vi / en / other."""
    if not value or not value.strip():
        return None
    primary = re.split(r"[-_]", value.strip().lower(), maxsplit=1)[0]
    if primary == "vi":
        return "vi"
    if primary == "en":
        return "en"
    return "other"


def detect_text(text: str) -> Lang | None:
    """Heuristic over the text alone; None when there is too little text to judge."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < MIN_LETTERS:
        return None

    vi_letters = 0
    strong_vi = 0
    for c in letters:
        if c in "đĐ":
            vi_letters += 1
            strong_vi += 1
            continue
        marks = {m for m in unicodedata.normalize("NFD", c)[1:]}
        if marks & _VI_DIACRITIC_MARKS:
            vi_letters += 1
        if marks & _VI_MARKS:
            strong_vi += 1
    if strong_vi and vi_letters / len(letters) >= VI_RATIO:
        return "vi"

    ascii_ratio = sum(c.isascii() for c in letters) / len(letters)
    words = [w.lower() for w in _WORD.findall(text)]
    stop_ratio = sum(w in _EN_STOPWORDS for w in words) / len(words) if words else 0.0
    if ascii_ratio >= EN_ASCII_RATIO and stop_ratio >= EN_STOPWORD_RATIO:
        return "en"
    return "other"


def detect_lang(text: str, declared: list[str | None]) -> Lang | None:
    """Text wins when it clearly says vi or en; otherwise the first declared language;
    otherwise the text verdict ("other") or None."""
    from_text = detect_text(text)
    if from_text in ("vi", "en"):
        return from_text
    for value in declared:
        normalized = normalize_declared(value)
        if normalized:
            return normalized
    return from_text
