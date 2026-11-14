"""Hugging Face Daily Papers: community-upvoted AI papers."""

import logging
from datetime import datetime

import httpx

from digest.fetchers.text import excerpt
from digest.http import get_with_retry
from digest.models import Item, item_id

API = "https://huggingface.co/api/daily_papers"
PAPER = "https://huggingface.co/papers/{}"
SOURCE = "hf_papers"

log = logging.getLogger(__name__)


async def fetch(client: httpx.AsyncClient, *, now: datetime) -> list[Item]:
    """Today's list. `now` is unused but kept for the common fetcher signature."""
    try:
        response = await get_with_retry(client, API)
        return [_to_item(entry) for entry in response.json() if entry.get("paper")]
    except Exception as exc:  # fetchers never raise
        log.warning("hf_papers: fetch failed: %s", exc)
        return []


def _to_item(entry: dict) -> Item:
    paper = entry["paper"]
    url = PAPER.format(paper["id"])
    # The paper's own publishedAt is the arXiv date; it reached the daily list later.
    published = paper.get("submittedOnDailyAt") or entry.get("publishedAt") or paper["publishedAt"]
    return Item(
        id=item_id(url),
        source=SOURCE,
        title=paper.get("title") or entry["title"],
        url=url,
        score=paper.get("upvotes") or 0,
        comments=entry.get("numComments") or 0,
        published_at=datetime.fromisoformat(published),
        tags=list(paper.get("ai_keywords") or []),
        description=excerpt(paper.get("ai_summary") or paper.get("summary")),
    )
