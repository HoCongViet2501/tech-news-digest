"""Render a Digest to Telegram HTML messages, static web pages and an RSS feed."""

from datetime import date, timedelta
from email.utils import format_datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader
from markupsafe import escape

from digest.feedback.weekly import WeeklyReport
from digest.models import Digest, TelegramMessage

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
        "ai_unavailable": "AI không khả dụng hôm nay, tin được xếp hạng theo heuristic.",
        "radar": "Radar thư viện",
        "news": "Tin tức",
        "installed": "đang dùng",
        "fixed_in": "đã sửa ở",
        "major": "bản major mới",
        "breaking": "có breaking change",
        "action_required": "Cần sửa code khi nâng cấp.",
        "no_action": "Nâng cấp không cần sửa code.",
        "weekly_title": "Tổng kết tuần",
        "weekly_items": "tin",
        "weekly_votes": "lượt bình chọn",
        "weekly_liked": "thích",
        "weekly_best_source": "nguồn hữu ích nhất",
        "weekly_empty": "Tuần này chưa có bản tin nào.",
        "weekly_suggestions": "Gợi ý chỉnh profile",
    },
    "en": {
        "title": "Tech digest",
        "discussion": "discussion",
        "also_on": "also on",
        "archive": "Archive",
        "empty": "Nothing new today.",
        "feed": "RSS",
        "all_failed": "No source could be fetched today. Check the GitHub Actions log.",
        "ai_unavailable": "AI unavailable today; items are ranked by heuristic.",
        "radar": "Dependency radar",
        "news": "News",
        "installed": "installed",
        "fixed_in": "fixed in",
        "major": "new major",
        "breaking": "breaking changes",
        "action_required": "Upgrading needs code changes.",
        "no_action": "Upgrading needs no code changes.",
        "weekly_title": "Weekly recap",
        "weekly_items": "items",
        "weekly_votes": "votes",
        "weekly_liked": "liked",
        "weekly_best_source": "most useful source",
        "weekly_empty": "No digest was sent this week.",
        "weekly_suggestions": "Profile suggestions",
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
_env.filters["score10"] = lambda value: f"{value:g}/10"
# RSS <description> holds HTML: escape once for HTML (plain str), autoescape adds the XML layer.
_env.filters["html_text"] = lambda value: str(escape(value))


def telegram_messages(
    digest: Digest, language: str, buttons: bool = False
) -> list[TelegramMessage]:
    """parse_mode=HTML messages, each <= 4096 chars; items are never split.

    With buttons, each message lists the numbered items it contains so delivery can
    attach one like/dislike row per item.
    """
    macros = _env.get_template("telegram.j2").module
    t = labels(language)
    header = str(macros.header(digest, t)).strip()
    # (text, (number, item id) or None)
    blocks: list[tuple[str, tuple[int, str] | None]] = [
        (str(macros.item(n, item, t)).strip(), (n, item.id))
        for n, item in enumerate(digest.items, 1)
    ]
    if not blocks:
        blocks = [(t["empty"], None)]
    if digest.radar:
        # Section headings ride on the first block of each section so they never end a message.
        radar = [(str(macros.radar_entry(entry, t)).strip(), None) for entry in digest.radar]
        radar[0] = (f"<b>📡 {escape(t['radar'])}</b>\n{radar[0][0]}", None)
        blocks[0] = (f"<b>📰 {escape(t['news'])}</b>\n{blocks[0][0]}", blocks[0][1])
        blocks = radar + blocks

    messages: list[TelegramMessage] = []
    current = TelegramMessage(text=header)
    for text, ref in blocks:
        candidate = f"{current.text}\n\n{text}" if current.text else text
        if len(candidate) > TELEGRAM_LIMIT and current.text:
            messages.append(current)
            current = TelegramMessage(text=text)
        else:
            current.text = candidate
        if buttons and ref is not None:
            current.buttons.append(ref)
    messages.append(current)
    return messages


def render_telegram(digest: Digest, language: str) -> list[str]:
    return [m.text for m in telegram_messages(digest, language)]


def render_weekly(report: WeeklyReport, language: str) -> str:
    """One message: 5 items and a few suggestions stay far below the 4096-char limit."""
    macros = _env.get_template("weekly.j2").module
    return str(macros.weekly(report, labels(language))).strip()


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
