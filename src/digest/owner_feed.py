"""The daily Telegram digest from the owner's My Feed (docs/PLAN.md §16 W4 step 6).

The owner is named in config (`delivery.telegram.owner_github_login` / `owner_email`)
and looked up in auth.users. When found and following tags, the digest is the top
`max_items` of public.feed_for_user (the same SQL ranking as the web's My Feed), minus
items already sent (seen.json). Each item's summary is its stored TL;DR when it has one.

Every "no" here (no account, no followed tags, nothing new) returns None, and the caller
sends the Part 1 digest instead. The owner's user id never reaches a log line.
"""

import logging
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from digest.config import Config
from digest.models import ScoredItem
from digest.store.models import FeedCard

log = logging.getLogger(__name__)

FEED_MAX = 60  # feed_for_user's cap; leaves room to skip items sent on earlier days


class OwnerFeedStore(Protocol):
    def find_user(self, *, github_login: str, email: str) -> UUID | None:
        """The one auth user with this GitHub login or confirmed email (case-insensitive;
        empty values never match); None when there is none or more than one."""
        ...

    def follows_tags(self, user_id: UUID) -> bool: ...

    def feed_for_user(self, user_id: UUID, n: int) -> list[FeedCard]:
        """public.feed_for_user(user_id, n), in rank order, with items.tldr."""
        ...


def owner_configured(cfg: Config) -> bool:
    tg = cfg.delivery.telegram
    return bool(tg.owner_github_login.strip() or tg.owner_email.strip())


def card_to_item(card: FeedCard) -> ScoredItem:
    others = list(
        dict.fromkeys(m.source_name for m in card.mentions if m.source_id != card.source_id)
    )
    tldr = card.tldr.strip() if card.tldr else None
    return ScoredItem(
        id=card.id,
        source=card.source_name,
        title=card.title,
        url=card.url,
        discussion_url=card.discussion_url,
        score=card.upvotes,
        comments=card.comments,
        published_at=card.published_at,
        also_on=others,
        description=card.description,
        relevance=0.0,  # the order is the ranking; reason None keeps the score off the card
        summary=tldr or None,
    )


def owner_items(
    store: OwnerFeedStore, cfg: Config, seen: Mapping[str, str]
) -> tuple[list[ScoredItem], int] | None:
    """The owner's top unsent items and how many the feed returned, or None to fall back
    to the Part 1 digest."""
    tg = cfg.delivery.telegram
    user_id = store.find_user(
        github_login=tg.owner_github_login.strip(), email=tg.owner_email.strip()
    )
    if user_id is None:
        log.info("my feed: owner account not found; sending the Part 1 digest")
        return None
    if not store.follows_tags(user_id):
        log.info("my feed: owner follows no tags yet; sending the Part 1 digest")
        return None
    cards = store.feed_for_user(user_id, FEED_MAX)
    fresh = [c for c in cards if c.id not in seen][: cfg.max_items]
    if not fresh:
        log.info("my feed: %d items, none new; sending the Part 1 digest", len(cards))
        return None
    return [card_to_item(c) for c in fresh], len(cards)
