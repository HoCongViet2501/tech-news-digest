"""Pydantic models crossing the content-store boundary (Postgres tables in supabase/)."""

from collections.abc import Sequence
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
    created_at: AwareDatetime | None = None


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


def normalize_native_tags(tags: Sequence[str]) -> list[str]:
    """Lowercase, trim, join inner whitespace with '-', drop empties and duplicates.

    Taxonomy aliases are matched exactly against these, so "Large Language Models" from
    HF Papers and "large-language-models" from dev.to end up the same.
    """
    out: dict[str, None] = {}
    for tag in tags:
        norm = "-".join(tag.lower().split())
        if norm:
            out[norm] = None
    return list(out)


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
    native_tags: list[str] = Field(default_factory=list)  # the source's own tags, normalized

    @classmethod
    def from_item(cls, item: Item, source_id: int, fetched_at: datetime) -> Self:
        return cls(
            id=item.id,
            source_id=source_id,
            title=item.title,
            url=item.url,
            discussion_url=item.discussion_url,
            description=item.description,
            published_at=item.published_at,
            fetched_at=fetched_at,
            native_score=item.score,
            native_comments=item.comments,
            native_tags=normalize_native_tags(item.tags),
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


class Mention(_Model):
    """An item_mentions row: one source that listed the item, with its own popularity."""

    item_id: str
    source_id: int
    discussion_url: str | None = None
    native_score: int = 0
    native_comments: int = 0
    first_seen_at: AwareDatetime


class UpsertResult(_Model):
    new_ids: list[str] = Field(default_factory=list)  # inserted by this call, in input order
    refreshed: int = 0  # already known from the same source; popularity refreshed
    # Already known from another source (same canonical URL): the items row keeps the
    # first source's popularity (pop_pct is per source); this source's numbers go to its
    # item_mentions row, and its native tags are merged into the item's.
    other_source: int = 0


class EnrichmentUpdate(_Model):
    """What the enrich stage writes for one item. None leaves the stored value as it is;
    description is only filled when the item has none."""

    id: str = Field(pattern=r"^[0-9a-f]{40}$")
    image_url: str | None = None
    description: str | None = None
    reading_min: int | None = Field(default=None, ge=0, le=32767)
    lang: str | None = None


class TagSpec(_Model):
    """A tag as synced from taxonomy.yaml into the tags table; slug is its natural key."""

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    parent: str | None = None
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)


class Tag(TagSpec):
    """A tags row. Tags removed from the YAML are retired (active = false), never deleted."""

    active: bool = True


class TagMethod(StrEnum):
    """Which tagger step assigned a tag (docs/PLAN.md §15 Tagging)."""

    NATIVE = "native"
    RULE = "rule"
    EMBED = "embed"
    LLM = "llm"


class ItemTag(_Model):
    tag: str
    confidence: float = Field(ge=0, le=1)
    method: TagMethod


class StoredItemTag(ItemTag):
    item_id: str


class TagUpdate(_Model):
    """What a tagging step writes for one item: its tags from `methods` are replaced by
    `tags` (each of which must use one of those methods); tags from other methods stay.
    `quality` None keeps the stored value; `done` sets tagged_at."""

    id: str = Field(pattern=r"^[0-9a-f]{40}$")
    methods: frozenset[TagMethod]
    tags: list[ItemTag] = Field(default_factory=list)
    quality: float | None = Field(default=None, ge=0, le=1)
    done: bool = False

    @model_validator(mode="after")
    def _tags_within_methods(self) -> Self:
        stray = {t.method for t in self.tags} - self.methods
        if stray:
            raise ValueError(f"tags use methods outside {sorted(self.methods)}: {sorted(stray)}")
        slugs = [t.tag for t in self.tags]
        if len(set(slugs)) != len(slugs):
            raise ValueError(f"duplicate tags: {slugs}")
        return self


class Cluster(_Model):
    """A clusters row: near-duplicate items (docs/PLAN.md §15 Dedupe)."""

    id: int
    canonical_item_id: str
    item_ids: list[str]
