"""Weekly profile suggestions from feedback. Only suggests; config.yaml is never edited."""

import json

from pydantic import BaseModel

from digest.ai.llm import Completion, LLMChain
from digest.ai.prompts import language_name, load_prompt
from digest.config import Config
from digest.feedback.weekly import ProfileSuggestion
from digest.models import Vote


class _SuggestionResponse(BaseModel):
    suggestions: list[ProfileSuggestion]


def suggest_profile_changes(
    chain: LLMChain, cfg: Config, votes: list[Vote]
) -> tuple[list[ProfileSuggestion], Completion[_SuggestionResponse]]:
    """`votes`: latest vote per item, newest first. Raises AIUnavailable."""
    votes_json = json.dumps(
        [{"vote": v.vote, "title": v.title, "source": v.source} for v in votes],
        ensure_ascii=False,
    )
    prompt = load_prompt(
        "profile",
        profile=cfg.profile.strip(),
        votes_json=votes_json,
        max_suggestions=str(cfg.weekly.max_profile_suggestions),
        output_language=language_name(cfg.ai.output_language),
    )
    completion = chain.complete([{"role": "user", "content": prompt}], _SuggestionResponse)
    return completion.data.suggestions[: cfg.weekly.max_profile_suggestions], completion
