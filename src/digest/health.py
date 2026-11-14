"""`python -m digest health`: warn on Telegram about sources failing for more than 24 h.

Run once a day from daily.yml, so it sends at most one message per day, and only when
something is wrong (docs/PLAN.md §13 Ingestion step 10).
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from html import escape

from digest.render import TELEGRAM_LIMIT, labels
from digest.store import Source

STALE_AFTER = timedelta(hours=24)
ERROR_CHARS = 120


def stale_sources(
    sources: Sequence[Source], now: datetime, *, max_age: timedelta = STALE_AFTER
) -> list[Source]:
    """Enabled sources with no success for more than `max_age` (counted from creation
    when they never succeeded) that are either failing or no longer being fetched.

    "Failing" means the last fetch recorded an error. A source whose last fetch was fine
    is only reported once it is also overdue by more than `max_age` past its cadence
    (ingest stopped running): a daily feed fetched 24 h and a few minutes ago is just
    waiting for its next run, not broken.
    """
    stale = []
    for source in sources:
        since = source.last_ok_at or source.created_at
        if not source.enabled or since is None:
            continue
        age = now - since
        if age <= max_age:
            continue
        overdue = age > timedelta(hours=source.cadence_hours) + max_age
        if source.last_error is not None or overdue:
            stale.append(source)
    return stale


def render_health(stale: Sequence[Source], now: datetime, language: str) -> str:
    """One Telegram message (HTML) listing the stale sources; cut to fit one message."""
    t = labels(language)
    header = f"⚠️ <b>{escape(t['health_title'])} · {now.date().isoformat()}</b>"
    lines = [header]
    for n, source in enumerate(stale):
        last_ok = source.last_ok_at.date().isoformat() if source.last_ok_at else t["health_never"]
        error = source.last_error or t["health_not_fetched"]
        if len(error) > ERROR_CHARS:
            error = error[: ERROR_CHARS - 1] + "…"
        line = (
            f"• <b>{escape(source.name)}</b> ({escape(source.kind)}) · "
            f"{escape(t['health_last_ok'])} {escape(last_ok)}\n  <i>{escape(error)}</i>"
        )
        more = f"… +{len(stale) - n}"
        if len("\n".join([*lines, line, more])) > TELEGRAM_LIMIT:
            lines.append(more)
            break
        lines.append(line)
    return "\n".join(lines)
