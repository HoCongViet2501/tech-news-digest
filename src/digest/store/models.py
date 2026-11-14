"""Pydantic models crossing the content-store boundary (Postgres tables in supabase/)."""

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from digest.models import Item


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Stage(StrEnum):
    """Pipeline stages tracked by a nullable timestamp column on items (the work queue)."""

    ENRICH = "enrich"
    TAG = "tag"
    EMBED = "embed"
    SUMMARIZE = "summarize"

    @property
    def column(self) -> str:
        return STAGE_COLUMNS[self]


STAGE_COLUMNS: dict[Stage, str] = {
    Stage.ENRICH: "enriched_at",
    Stage.TAG: "tagged_at",
    Stage.EMBED: "embedded_at",
    Stage.SUMMARIZE: "summarized_at",
}


class SourceSpec(_Model):
    """A source as configured in sources.yaml; (kind, url) is its natural key."""

    kind: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    category: str | None = None
    base_weight: int = Field(default=50, ge=0, le=100)
    cadence_hours: int = Field(default=24, gt=0)

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.url)


class Source(SourceSpec):
    """A row of the sources table: the spec plus its id and fetch state."""

    id: int
    enabled: bool = True
    etag: str | None = None
    last_modified: str | None = None
    last_fetched_at: AwareDatetime | None = None
    last_ok_at: AwareDatetime | None = None
    last_error: str | None = None


class SyncResult(_Model):
    inserted: int = 0
    updated: int = 0  # spec changed, or re-enabled
    disabled: int = 0  # missing from the YAML

    @property
    def changed(self) -> bool:
        return bool(self.inserted or self.updated or self.disabled)


class FetchOutcome(_Model):
    """Result of one fetch of one source. A 304 counts as ok."""

    at: AwareDatetime
    ok: bool
    etag: str | None = None  # None keeps the stored value
    last_modified: str | None = None  # None keeps the stored value
    error: str | None = None

    @model_validator(mode="after")
    def _error_iff_failed(self) -> Self:
        if self.ok and self.error is not None:
            raise ValueError("an ok outcome cannot carry an error")
        if not self.ok and not self.error:
            raise ValueError("a failed outcome needs an error message")
        return self


class NewItem(_Model):
    """What the fetch step writes. Enrichment, tags, embeddings, etc. are filled by later stages."""

    id: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_id: int
    title: str
    url: str
    discussion_url: str | None = None
    description: str | None = None
    published_at: AwareDatetime
    fetched_at: AwareDatetime
    native_score: int = 0
    native_comments: int = 0

    @classmethod
    def from_item(cls, item: Item, source_id: int, fetched_at: datetime) -> Self:
        return cls(
            id=item.id,
            source_id=source_id,
            title=item.title,
            url=item.url,
            discussion_url=item.discussion_url,
            # Item.description is being added in a parallel W1 step; tolerate its absence.
            description=getattr(item, "description", None),
            published_at=item.published_at,
            fetched_at=fetched_at,
            native_score=item.score,
            native_comments=item.comments,
        )


class StoredItem(NewItem):
    """An items row as read back by pipeline stages (embedding and fts are not loaded)."""

    cluster_id: int | None = None
    image_url: str | None = None
    lang: str | None = None
    reading_min: int | None = None
    pop_pct: float | None = None
    quality: float | None = None
    tldr: str | None = None
    first_seen_at: AwareDatetime
    enriched_at: AwareDatetime | None = None
    tagged_at: AwareDatetime | None = None
    embedded_at: AwareDatetime | None = None
    summarized_at: AwareDatetime | None = None


class UpsertResult(_Model):
    new_ids: list[str] = Field(default_factory=list)  # inserted by this call, in input order
    refreshed: int = 0  # already known; native_score/comments/fetched_at refreshed
