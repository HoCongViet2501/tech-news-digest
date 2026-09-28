"""OpenAI-compatible provider chain: try each provider in order until one returns valid JSON.

Keys are read from the env vars named in config and are never logged.
"""

import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import openai
from pydantic import TypeAdapter, ValidationError

from digest.config import AIConfig

TIMEOUT_SECONDS = 60
FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")

log = logging.getLogger(__name__)


class AIUnavailable(Exception):
    """No provider produced a valid answer."""


class ChatClient(Protocol):
    chat: Any


@dataclass(frozen=True)
class Provider:
    name: str
    model: str
    client: ChatClient


@dataclass(frozen=True)
class Completion[T]:
    data: T
    provider: str
    prompt_tokens: int
    completion_tokens: int


def parse_json[T](text: str, schema: type[T] | Any) -> T:
    """Strip code fences, cut from the first '[' or '{' to its last partner, validate."""
    body = FENCE.sub("", text.strip())
    starts = [i for i in (body.find("{"), body.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON object or array in response")
    start = min(starts)
    end = body.rfind("}" if body[start] == "{" else "]")
    if end < start:
        raise ValueError("unterminated JSON in response")
    try:
        return TypeAdapter(schema).validate_json(body[start : end + 1])
    except ValidationError as exc:
        raise ValueError(f"response failed validation: {exc.error_count()} error(s)") from exc


def _openai_client(base_url: str, api_key: str) -> ChatClient:
    return openai.OpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=0)


class LLMChain:
    def __init__(self, providers: list[Provider]) -> None:
        self.providers = providers

    @classmethod
    def from_config(
        cls,
        cfg: AIConfig,
        env: Mapping[str, str] = os.environ,
        client_factory: Callable[[str, str], ChatClient] = _openai_client,
    ) -> "LLMChain":
        providers = []
        for p in cfg.providers:
            key = env.get(p.api_key_env)
            if not key:
                log.info("ai: skipping provider %s, %s is not set", p.name, p.api_key_env)
                continue
            providers.append(Provider(p.name, p.model, client_factory(p.base_url, key)))
        return cls(providers)

    def complete[T](
        self, messages: list[dict[str, str]], schema: type[T] | Any, json_mode: bool = True
    ) -> Completion[T]:
        for provider in self.providers:
            kwargs: dict[str, Any] = {"model": provider.model, "messages": messages}
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                response = provider.client.chat.completions.create(**kwargs)
                data = parse_json(response.choices[0].message.content or "", schema)
            except openai.APIError as exc:
                log.warning("ai: %s failed (%s), trying next", provider.name, type(exc).__name__)
                continue
            except ValueError as exc:
                log.warning("ai: %s returned unusable JSON (%s), trying next", provider.name, exc)
                continue
            usage = getattr(response, "usage", None)
            result = Completion(
                data=data,
                provider=provider.name,
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            )
            log.info(
                "ai: %s answered (%d in / %d out tokens)",
                provider.name,
                result.prompt_tokens,
                result.completion_tokens,
            )
            return result
        raise AIUnavailable("no AI provider produced a valid answer")
