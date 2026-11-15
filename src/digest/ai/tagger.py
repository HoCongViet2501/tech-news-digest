"""Step 4 of the tagger: one LLM call tags a batch of items and scores their quality.

The model may only pick taxonomy slugs; anything else in its answer is dropped.
"""

import json
import logging
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from digest.ai.llm import LLMChain
from digest.ai.prompts import load_prompt
from digest.taxonomy import Taxonomy

log = logging.getLogger(__name__)

SHORT_ID = 8  # id prefix sent to the model; lengthened if two items in a batch collide
MAX_TAGS = 3


class LLMTagItem(BaseModel):
    """What the model sees of one item."""

    id: str
    title: str
    description: str | None = None
    source: str = ""
    native_tags: list[str] = Field(default_factory=list)
    url: str | None = None


class LLMTagAnswer(BaseModel):
    tags: list[str]
    quality: float


class _Answer(BaseModel):
    id: str
    tags: list[str] = Field(default_factory=list)
    quality: float | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def _none_is_empty(cls, value: object) -> object:
        return [] if value is None else value


class _Response(BaseModel):
    items: list[_Answer]


def _short_ids(ids: list[str]) -> dict[str, str]:
    length = SHORT_ID
    while len({i[:length] for i in ids}) < len(set(ids)):
        length += 4
    return {i[:length]: i for i in ids}


def taxonomy_lines(taxonomy: Taxonomy) -> str:
    return "\n".join(f"- {t.slug}: {t.name}. {t.description}" for t in taxonomy.tags)


def llm_tag_batch(
    chain: LLMChain, taxonomy: Taxonomy, items: list[LLMTagItem]
) -> dict[str, LLMTagAnswer]:
    """Tags (known slugs only, at most MAX_TAGS) and quality (0-1) per item id. Items the
    model skipped, or answered without a quality, are left out. Raises AIUnavailable."""
    by_short = _short_ids([i.id for i in items])
    to_short = {full: short for short, full in by_short.items()}
    payload = [
        {
            "id": to_short[i.id],
            "title": i.title,
            "description": i.description,
            "source": i.source,
            "native_tags": i.native_tags,
            "domain": (urlsplit(i.url).hostname or "").removeprefix("www.") if i.url else None,
        }
        for i in items
    ]
    prompt = load_prompt(
        "tag",
        taxonomy=taxonomy_lines(taxonomy),
        items_json=json.dumps(
            [{k: v for k, v in p.items() if v} for p in payload], ensure_ascii=False
        ),
    )
    completion = chain.complete([{"role": "user", "content": prompt}], _Response)

    known = taxonomy.by_slug
    out: dict[str, LLMTagAnswer] = {}
    dropped: list[str] = []
    for answer in completion.data.items:
        full = by_short.get(answer.id)
        if full is None or answer.quality is None:
            continue
        slugs = []
        for raw in answer.tags:
            slug = "-".join(raw.strip().lower().replace("_", " ").split())
            if slug in known:
                if slug not in slugs:
                    slugs.append(slug)
            else:
                dropped.append(raw)
        quality = max(0.0, min(1.0, answer.quality))
        out[full] = LLMTagAnswer(tags=slugs[:MAX_TAGS], quality=quality)
    if dropped:
        log.info("tag: dropped %d unknown slug(s) from the LLM: %s", len(dropped), dropped[:5])
    return out
