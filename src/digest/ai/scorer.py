"""Score the heuristic candidates against the owner's profile in a single request."""

import json
from collections.abc import Sequence
from urllib.parse import urlsplit

from pydantic import BaseModel

from digest.ai.llm import Completion, LLMChain
from digest.ai.prompts import language_name, load_prompt
from digest.config import Config
from digest.models import ScoredItem

SHORT_ID = 8  # id prefix sent to the model; saves tokens, unique among ~60 items


class _Score(BaseModel):
    id: str
    score: float
    reason: str | None = None


class _ScoreResponse(BaseModel):
    items: list[_Score]


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, value))


def feedback_section(liked: Sequence[str], disliked: Sequence[str]) -> str:
    """Prompt block with the owner's recent votes; empty when there are none."""
    if not liked and not disliked:
        return ""
    lines = ["", "Their recent reactions to past digests (calibrate with them, not hard rules):"]
    for label, titles in (("Liked", liked), ("Disliked", disliked)):
        if titles:
            lines.append(f"{label}:")
            lines += [f"- {title}" for title in titles]
    return "\n".join(lines) + "\n"


def score_items(
    chain: LLMChain,
    cfg: Config,
    candidates: list[ScoredItem],
    liked: Sequence[str] = (),
    disliked: Sequence[str] = (),
) -> tuple[list[ScoredItem], Completion[_ScoreResponse]]:
    """AI relevance replaces the heuristic; unscored items keep it (clamped to 0-10).

    Sorted by AI score with the heuristic as tie-breaker, cut to max_items.
    Raises AIUnavailable when no provider answers.
    """
    by_short = {item.id[:SHORT_ID]: item for item in candidates}
    items_json = json.dumps(
        [
            {
                "id": short,
                "title": item.title,
                "source": item.source,
                "score": item.score,
                "domain": (urlsplit(item.url).hostname or "").removeprefix("www."),
            }
            for short, item in by_short.items()
        ],
        ensure_ascii=False,
    )
    prompt = load_prompt(
        "score",
        profile=cfg.profile.strip(),
        output_language=language_name(cfg.ai.output_language),
        items_json=items_json,
        feedback=feedback_section(liked, disliked),
    )
    completion = chain.complete([{"role": "user", "content": prompt}], _ScoreResponse)

    ai = {s.id: s for s in completion.data.items if s.id in by_short}
    rescored = []
    for short, item in by_short.items():
        heuristic = item.relevance
        if short in ai:
            update = {"relevance": _clamp(ai[short].score), "reason": ai[short].reason}
        else:
            update = {"relevance": _clamp(heuristic)}
        rescored.append((item.model_copy(update=update), heuristic))
    rescored.sort(key=lambda pair: (pair[0].relevance, pair[1]), reverse=True)
    return [item for item, _ in rescored[: cfg.max_items]], completion
