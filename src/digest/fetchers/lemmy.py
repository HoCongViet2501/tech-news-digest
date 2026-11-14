"""Top posts of the day on a Lemmy instance (programming.dev by default)."""

import logging
from datetime import datetime

import httpx

from digest.fetchers.text import excerpt
from digest.http import get_with_retry
from digest.models import Item, item_id

INSTANCE = "https://programming.dev"
SOURCE = "lemmy"

log = logging.getLogger(__name__)


async def fetch(
    client: httpx.AsyncClient,
    *,
    now: datetime,
    instance: str = INSTANCE,
    sort: str = "TopDay",
    limit: int = 50,
) -> list[Item]:
    """`now` is unused but kept for the common fetcher signature."""
    params: dict[str, str | int] = {"sort": sort, "limit": limit}
    try:
        response = await get_with_retry(client, f"{instance}/api/v3/post/list", params=params)
        posts = response.json()["posts"]
        return [_to_item(view, instance) for view in posts if _visible(view)]
    except Exception as exc:  # fetchers never raise
        log.warning("lemmy: fetch failed: %s", exc)
        return []


def _visible(view: dict) -> bool:
    post = view["post"]
    return not (
        post.get("nsfw")
        or post.get("removed")
        or post.get("deleted")
        or view.get("community", {}).get("nsfw")
    )


def _to_item(view: dict, instance: str) -> Item:
    post, counts = view["post"], view.get("counts", {})
    discussion = f"{instance}/post/{post['id']}"
    link = post.get("url")
    # Text posts have no link, and image posts link a bare image: use the thread instead.
    if not link or (post.get("url_content_type") or "").startswith("image/"):
        link = discussion
    community = view.get("community", {}).get("name")
    return Item(
        id=item_id(link),
        source=SOURCE,
        title=post["name"],
        url=link,
        discussion_url=discussion,
        score=counts.get("score") or 0,
        comments=counts.get("comments") or 0,
        published_at=datetime.fromisoformat(post["published"]),
        tags=[community] if community else [],
        description=excerpt(post.get("body")),
    )
