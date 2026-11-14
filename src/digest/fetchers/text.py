"""Turn source-provided HTML or Markdown snippets into a short plain-text description."""

import re
import warnings

from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

DESCRIPTION_CHARS = 300
_WHITESPACE = re.compile(r"\s+")


def excerpt(text: str | None, limit: int = DESCRIPTION_CHARS) -> str | None:
    """Strip HTML, collapse whitespace and cut at a word boundary; None if nothing is left."""
    if not text:
        return None
    if "<" in text or "&" in text:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", MarkupResemblesLocatorWarning)
            text = BeautifulSoup(text, "html.parser").get_text(" ")
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut[limit // 2 :]:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,.;:-") + "…"
