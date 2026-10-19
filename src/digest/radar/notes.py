"""AI condenses release notes for reported releases (vulnerabilities need no AI)."""

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
) -> tuple[list[RadarEntry], Completion[_NotesResponse] | None]:
    """One request for every release entry; on failure entries are returned unchanged."""
    releases = [e for e in entries if e.kind != "vulnerability"]
    if not releases:
        return entries, None
    items_json = json.dumps(
        [
            {
                "id": e.key,
                "package": e.package,
                "installed": e.installed,
                "release": e.version,
                "major": e.kind == "major",
                "title": e.title,
                "notes": (e.notes or "")[: cfg.radar.notes_max_chars],
            }
            for e in releases
        ],
        ensure_ascii=False,
    )
    prompt = load_prompt(
        "radar",
        profile=cfg.profile.strip(),
        output_language=language_name(cfg.ai.output_language),
        items_json=items_json,
    )
    try:
        completion = chain.complete([{"role": "user", "content": prompt}], _NotesResponse)
    except AIUnavailable:
        log.warning("radar: AI unavailable, %d releases left without summaries", len(releases))
        return entries, None
    notes = {n.id: n for n in completion.data.items}
    updated = [
        e.model_copy(
            update={
                "summary": notes[e.key].summary,
                "action_required": notes[e.key].action_required,
            }
        )
        if e.kind != "vulnerability" and e.key in notes
        else e
        for e in entries
    ]
    return updated, completion
