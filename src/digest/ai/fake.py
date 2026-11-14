"""Deterministic stand-in for the LLM providers, used when DIGEST_OFFLINE=1.

It answers the real prompts (loaded from ai/prompts/) with valid JSON derived only
from the items in the prompt, so offline runs exercise scoring, summarizing, JSON
parsing and rendering without network access, API keys or quota.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from digest.ai.llm import LLMChain, Provider
from digest.ai.prompts import load_prompt
from digest.config import AIConfig

PROVIDER_NAME = "offline-fake"
ITEMS_MARKER = "\nItems:\n"
PREFIX = "[offline]"

log = logging.getLogger(__name__)

# User-facing text per output language; English is the fallback.
TEXTS: dict[str, dict[str, str]] = {
    "vi": {
        "reason": "Điểm giả lập từ nguồn {source}.",
        "summary": "Tóm tắt giả lập cho “{title}” ({source}), dựa trên {chars} ký tự nội dung.",
        "why": "Ghi chú giả lập: chỉ dùng để kiểm tra pipeline offline.",
    },
    "en": {
        "reason": "Simulated score for a {source} item.",
        "summary": "Simulated summary of “{title}” ({source}), from {chars} characters of content.",
        "why": "Simulated note: only used to exercise the offline pipeline.",
    },
}


def _digest(*parts: str) -> int:
    return int.from_bytes(hashlib.sha256("\x1f".join(parts).encode()).digest()[:8], "big")


def _score(item: dict[str, Any]) -> dict[str, Any]:
    score = _digest(str(item.get("id")), str(item.get("title"))) % 21 / 2  # 0.0-10.0 in 0.5 steps
    return {"id": item.get("id"), "score": score}


def _template_head(name: str) -> str:
    """The prompt text before its first placeholder, used to recognise the prompt type."""
    return load_prompt(name).split("{", 1)[0]


class FakeLLMClient:
    """OpenAI-compatible client whose replies depend only on the prompt it receives."""

    def __init__(self, output_language: str = "vi") -> None:
        self.texts = TEXTS.get(output_language, TEXTS["en"])
        self.handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            _template_head("score"): self._score,
            _template_head("summarize"): self._summarize,
        }
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _score(self, item: dict[str, Any]) -> dict[str, Any]:
        reason = self.texts["reason"].format(source=item.get("source", "?"))
        return {**_score(item), "reason": f"{PREFIX} {reason}"}

    def _summarize(self, item: dict[str, Any]) -> dict[str, Any]:
        summary = self.texts["summary"].format(
            title=item.get("title", ""),
            source=item.get("source", "?"),
            chars=len(item.get("content") or ""),
        )
        return {
            "id": item.get("id"),
            "summary": f"{PREFIX} {summary}",
            "why_it_matters": f"{PREFIX} {self.texts['why']}",
        }

    def reply(self, prompt: str) -> str:
        """JSON answer for a known prompt; '{}' (which fails validation) for anything else."""
        handler = next((h for head, h in self.handlers.items() if prompt.startswith(head)), None)
        if handler is None or ITEMS_MARKER not in prompt:
            return "{}"
        items = json.loads(prompt.rsplit(ITEMS_MARKER, 1)[1])
        return json.dumps({"items": [handler(item) for item in items]}, ensure_ascii=False)

    def _create(self, **kwargs: Any) -> SimpleNamespace:
        prompt = "\n".join(m["content"] for m in kwargs["messages"])
        content = self.reply(prompt)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(
                prompt_tokens=len(prompt) // 4, completion_tokens=len(content) // 4
            ),
        )


def fake_chain(cfg: AIConfig) -> LLMChain:
    log.info(
        "ai: offline mode, using the deterministic fake provider instead of %d real one(s)",
        len(cfg.providers),
    )
    client = FakeLLMClient(cfg.output_language)
    return LLMChain([Provider(PROVIDER_NAME, "fake", client)])
