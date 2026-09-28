import logging

import pytest
from pydantic import BaseModel

from digest.ai.llm import AIUnavailable, LLMChain, Provider, parse_json
from digest.config import AIConfig, AIProviderConfig
from fakes import FakeClient, rate_limited, server_error, timeout

MESSAGES = [{"role": "user", "content": "score these"}]


class Out(BaseModel):
    items: list[int]


def chain(*clients: FakeClient) -> LLMChain:
    return LLMChain([Provider(f"p{n}", f"model-{n}", c) for n, c in enumerate(clients, 1)])


# --- parse_json ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '{"items": [1, 2]}',
        '```json\n{"items": [1, 2]}\n```',
        '```\n{"items": [1, 2]}\n```',
        'Sure! Here is the JSON:\n{"items": [1, 2]}\nHope this helps.',
    ],
)
def test_parse_json_tolerates_fences_and_extra_text(text: str) -> None:
    assert parse_json(text, Out) == Out(items=[1, 2])


def test_parse_json_accepts_top_level_array_for_list_schema() -> None:
    assert parse_json("Result: [1, 2, 3] done", list[int]) == [1, 2, 3]


@pytest.mark.parametrize("text", ['{"items": [1, 2', "no json at all", '{"items": "x"}'])
def test_parse_json_rejects_broken_or_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        parse_json(text, Out)


# --- LLMChain ----------------------------------------------------------------------


def test_first_provider_answers() -> None:
    first, second = FakeClient('{"items": [1]}'), FakeClient('{"items": [2]}')

    result = chain(first, second).complete(MESSAGES, Out)

    assert result.data == Out(items=[1])
    assert result.provider == "p1"
    assert (result.prompt_tokens, result.completion_tokens) == (100, 20)
    assert second.calls == []


@pytest.mark.parametrize("error", [rate_limited(), server_error(), timeout()])
def test_falls_back_to_next_provider_on_api_error(error: Exception) -> None:
    first, second = FakeClient(error), FakeClient('{"items": [2]}')

    result = chain(first, second).complete(MESSAGES, Out)

    assert result.provider == "p2"
    assert result.data == Out(items=[2])
    assert len(first.calls) == 1


def test_broken_json_falls_back_instead_of_crashing() -> None:
    first, second = FakeClient('{"items": [1,'), FakeClient('```json\n{"items": [2]}\n```')

    assert chain(first, second).complete(MESSAGES, Out).provider == "p2"


def test_all_providers_failing_raises_ai_unavailable() -> None:
    with pytest.raises(AIUnavailable):
        chain(FakeClient(rate_limited()), FakeClient("not json")).complete(MESSAGES, Out)


def test_no_providers_raises_ai_unavailable() -> None:
    with pytest.raises(AIUnavailable):
        LLMChain([]).complete(MESSAGES, Out)


def test_json_mode_requests_json_object_and_uses_model() -> None:
    client = FakeClient('{"items": []}')

    chain(client).complete(MESSAGES, Out)

    call = client.calls[0]
    assert call["model"] == "model-1"
    assert call["messages"] == MESSAGES
    assert call["response_format"] == {"type": "json_object"}


def test_json_mode_off_sends_no_response_format() -> None:
    client = FakeClient('{"items": []}')

    chain(client).complete(MESSAGES, Out, json_mode=False)

    assert "response_format" not in client.calls[0]


def test_from_config_skips_providers_without_keys(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    cfg = AIConfig(
        enabled=True,
        providers=[
            AIProviderConfig(name="a", base_url="https://a/v1", model="m", api_key_env="A_KEY"),
            AIProviderConfig(name="b", base_url="https://b/v1", model="m", api_key_env="B_KEY"),
        ],
    )
    built: list[tuple[str, str]] = []

    def factory(base_url: str, api_key: str) -> FakeClient:
        built.append((base_url, api_key))
        return FakeClient()

    result = LLMChain.from_config(cfg, env={"B_KEY": "sk-secret-b"}, client_factory=factory)

    assert [p.name for p in result.providers] == ["b"]
    assert built == [("https://b/v1", "sk-secret-b")]
    assert "sk-secret-b" not in caplog.text
    assert "A_KEY" in caplog.text  # tells the owner which variable is missing
