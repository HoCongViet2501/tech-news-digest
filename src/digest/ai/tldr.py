"""One LLM call writes the TL;DR of a batch of items (docs/PLAN.md §13 step 9).

The answer is validated: ids must be ones that were sent, empty answers mean "not
enough to summarize", and overlong answers are cut at a sentence end.
"""

import json
import logging
import re

from pydantic import BaseModel, Field, field_validator

from digest.ai.llm import LLMChain
from digest.ai.prompts import language_name, load_prompt
from digest.ai.tagger import short_ids

log = logging.getLogger(__name__)

# A sentence ends at . ! ? or … followed by whitespace (or the end).
_SENTENCE_END = re.compile(r"[.!?…](?=\s|$)")


class TldrInput(BaseModel):
    """What the model sees of one item. `content` is the article text (transient, never
    stored) or, when the page could not be used, the stored description."""

    id: str
    title: str
    content: str


class _Answer(BaseModel):
    id: str
    tldr: str = ""

    @field_validator("tldr", mode="before")
    @classmethod
    def _none_is_empty(cls, value: object) -> object:
        return "" if value is None else value


class _Response(BaseModel):
    items: list[_Answer] = Field(default_factory=list)


def clean_tldr(text: str, max_chars: int) -> str:
    """Collapse whitespace; when longer than `max_chars`, keep the whole sentences that
    fit, or (one huge sentence) cut at a word and add an ellipsis."""
    text = " ".join(text.split()).strip()
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    ends = [m.end() for m in _SENTENCE_END.finditer(head)]
    if ends:
        return head[: ends[-1]].strip()
    head = head[:-1]  # room for the ellipsis
    cut = head.rsplit(" ", 1)[0] if " " in head else head
    return cut.rstrip(" ,.;:-") + "…"


def llm_tldr_batch(
    chain: LLMChain, items: list[TldrInput], *, output_language: str, max_chars: int
) -> dict[str, str]:
    """TL;DR per item id. Ids the model did not return are left out (retried later);
    an empty answer maps to "" (the model found nothing to summarize). Raises
    AIUnavailable when no provider produced valid JSON."""
    by_short = short_ids([i.id for i in items])
    to_short = {full: short for short, full in by_short.items()}
    payload = [{"id": to_short[i.id], "title": i.title, "content": i.content} for i in items]
    prompt = load_prompt(
        "tldr",
        output_language=language_name(output_language),
        items_json=json.dumps(payload, ensure_ascii=False),
    )
    completion = chain.complete([{"role": "user", "content": prompt}], _Response)

    out: dict[str, str] = {}
    unknown = 0
    for answer in completion.data.items:
        full = by_short.get(answer.id.strip())
        if full is None:
            unknown += 1
            continue
        if full not in out or not out[full]:  # a duplicate id never replaces a real answer
            out[full] = clean_tldr(answer.tldr, max_chars)
    if unknown:
        log.info("tldr: dropped %d answer(s) with an unknown id", unknown)
    return out
