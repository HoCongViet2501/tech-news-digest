"""Fake OpenAI-compatible clients for LLM tests; nothing here touches the network."""

from types import SimpleNamespace

import httpx
import openai


def rate_limited() -> openai.RateLimitError:
    request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
    return openai.RateLimitError(
        "rate limited", response=httpx.Response(429, request=request), body=None
    )


def server_error() -> openai.InternalServerError:
    request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
    return openai.InternalServerError(
        "boom", response=httpx.Response(503, request=request), body=None
    )


def timeout() -> openai.APITimeoutError:
    return openai.APITimeoutError(
        request=httpx.Request("POST", "https://llm.example/v1/chat/completions")
    )


class FakeClient:
    """Returns (or raises) the scripted replies in order; records every call."""

    def __init__(self, *replies: str | Exception) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )
