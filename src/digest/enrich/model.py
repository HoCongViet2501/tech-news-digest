from typing import Literal

from pydantic import BaseModel, Field

Lang = Literal["vi", "en", "other"]


class Enrichment(BaseModel):
    """What enrichment learned about one article page. Every field may be missing."""

    image_url: str | None = None
    description: str | None = None
    reading_min: int | None = None
    lang: Lang | None = None
    # Why the page was not (fully) enriched, e.g. "robots: disallowed". None on success.
    skip_reason: str | None = None
    # Full article text, kept in memory only for embeddings / TL;DR. It is excluded from
    # serialization so it can never be stored or displayed (PLAN §13 content rules).
    main_text: str | None = Field(default=None, exclude=True, repr=False)
