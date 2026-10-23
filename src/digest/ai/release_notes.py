"""Condense radar release notes into 1-2 sentences and answer "does upgrading need action?"."""

import json
import logging

from pydantic import BaseModel

from digest.ai.llm import AIUnavailable, Completion, LLMChain
from digest.ai.prompts import language_name, load_prompt
from digest.config import Config
from digest.models import RadarEntry

log = logging.getLogger(__name__)


class _Note(BaseModel):
    id: str
    summary: str
    action_required: bool | None = None


class _NotesResponse(BaseModel):
    items: list[_Note]


def summarize_releases(
    chain: LLMChain, cfg: Config, entries: list[RadarEntry]
) -> tuple[list[RadarEntry], list[Completion[_NotesResponse]]]:
    """Fill summary/action_required for releases with notes; a failed batch is left as is."""
    # Short positional ids: release ids contain slashes and @ that models like to mangle.
    todo = {f"r{n}": e for n, e in enumerate(entries, 1) if e.kind != "vulnerability" and e.notes}
    keys = list(todo)
    size = cfg.ai.summary_batch_size
    results: dict[str, _Note] = {}
    completions = []
    for start in range(0, len(keys), size):
        batch = keys[start : start + size]
        items_json = json.dumps(
            [
                {
                    "id": key,
                    "package": todo[key].package,
                    "installed": todo[key].installed,
                    "release": todo[key].version,
                    "notes": todo[key].notes,
                }
                for key in batch
            ],
            ensure_ascii=False,
        )
        prompt = load_prompt(
            "release_notes",
            profile=cfg.profile.strip(),
            output_language=language_name(cfg.ai.output_language),
            items_json=items_json,
        )
        try:
            completion = chain.complete([{"role": "user", "content": prompt}], _NotesResponse)
        except AIUnavailable:
            log.warning("radar: %d release notes left without a summary", len(batch))
            continue
        completions.append(completion)
        results.update({n.id: n for n in completion.data.items if n.id in batch})

    by_entry = {todo[key].id: note for key, note in results.items()}
    out = [
        e.model_copy(
            update={
                "summary": by_entry[e.id].summary,
                "action_required": by_entry[e.id].action_required,
            }
        )
        if e.id in by_entry
        else e
        for e in entries
    ]
    return out, completions
