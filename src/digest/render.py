"""Render a Digest to Telegram HTML messages, static web pages and an RSS feed."""

from datetime import date, timedelta
from email.utils import format_datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader
from markupsafe import escape

from digest.models import Digest

TELEGRAM_LIMIT = 4096
TITLE_MAX = 200
FEED_DAYS = 30

LABELS = {
    "vi": {
        "title": "Bản tin công nghệ",
        "discussion": "thảo luận",
        "also_on": "cũng có trên",
        "archive": "Lưu trữ",
        "empty": "Hôm nay không có tin mới.",
        "feed": "RSS",
        "all_failed": "Hôm nay không lấy được tin từ nguồn nào. Xem log GitHub Actions.",
    },
    "en": {
        "title": "Tech digest",
        "discussion": "discussion",
        "also_on": "also on",
        "archive": "Archive",
        "empty": "Nothing new today.",
        "feed": "RSS",
        "all_failed": "No source could be fetched today. Check the GitHub Actions log.",
    },
}


def labels(language: str) -> dict[str, str]:
    return LABELS.get(language, LABELS["en"])


def _truncate(text: str, limit: int = TITLE_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


_env = Environment(
    loader=PackageLoader("digest", "templates"),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
_env.filters["truncate_title"] = _truncate
_env.filters["rfc822"] = format_datetime


def render_telegram(digest: Digest, language: str) -> list[str]:
    """One or more parse_mode=HTML messages, each <= 4096 chars; items are never split."""
    macros = _env.get_template("telegram.j2").module
    t = labels(language)
    header = str(macros.header(digest, t)).strip()
    blocks = [str(macros.item(n, item, t)).strip() for n, item in enumerate(digest.items, 1)]
    if not blocks:
        blocks = [t["empty"]]

    messages: list[str] = []
    current = header
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > TELEGRAM_LIMIT and current:
            messages.append(current)
            current = block
        else:
            current = candidate
    messages.append(current)
    return messages


def render_warning(run_date: date, language: str) -> str:
    """Sent instead of an empty digest when every source failed."""
    t = labels(language)
    return f"⚠️ <b>{escape(t['title'])} · {run_date.isoformat()}</b>\n{escape(t['all_failed'])}"


def build_site(digests: list[Digest], out_dir: Path, base_url: str, language: str) -> None:
    """Write index.html, one YYYY-MM-DD.html per digest, and feed.xml (last 30 days)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(digests, key=lambda d: d.date, reverse=True)
    t = labels(language)
    common = {"t": t, "language": language, "archive": [d.date for d in ordered]}

    day_tpl = _env.get_template("day.html.j2")
    for digest in ordered:
        html = day_tpl.render(digest=digest, **common)
        (out_dir / f"{digest.date.isoformat()}.html").write_text(html, encoding="utf-8")

    latest = ordered[0] if ordered else None
    index = _env.get_template("index.html.j2").render(digest=latest, **common)
    (out_dir / "index.html").write_text(index, encoding="utf-8")

    recent = []
    if latest is not None:
        cutoff = latest.date - timedelta(days=FEED_DAYS)
        recent = [d for d in ordered if d.date > cutoff]
    feed = _env.get_template("feed.xml.j2").render(
        digests=recent, latest=latest, base_url=base_url, **common
    )
    (out_dir / "feed.xml").write_text(feed, encoding="utf-8")
