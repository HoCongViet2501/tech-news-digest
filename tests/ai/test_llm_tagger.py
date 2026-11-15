import json
from pathlib import Path

import pytest

from digest.ai.fake import fake_chain
from digest.ai.llm import AIUnavailable, LLMChain, Provider
from digest.ai.tagger import LLMTagItem, llm_tag_batch
from digest.config import Config
from digest.taxonomy import load_taxonomy
from fakes import FakeClient

TAXONOMY = load_taxonomy(Path(__file__).parents[2] / "taxonomy.yaml")
ITEMS = [
    LLMTagItem(id="a" * 40, title="Kafka consumer lag explained", source="dev.to"),
    LLMTagItem(id="b" * 40, title="Ten things", description="A list.", url="https://x.dev/p"),
]


def chain(client: FakeClient) -> LLMChain:
    return LLMChain([Provider("fake", "m", client)])


def test_unknown_slugs_are_dropped_and_quality_clamped() -> None:
    reply = {
        "items": [
            {
                "id": "a" * 8,
                "tags": ["kafka", "Kafka", "not-a-tag", "Messaging", "back_end", "backend", "ai"],
                "quality": 1.7,
            },
            {"id": "b" * 8, "tags": None, "quality": -0.2},
            {"id": "zzzzzzzz", "tags": ["ai"], "quality": 0.5},  # not in the batch
        ]
    }
    client = FakeClient(json.dumps(reply))

    answers = llm_tag_batch(chain(client), TAXONOMY, ITEMS)

    # Normalized ("back_end" is not a slug, "Messaging" is), deduped, at most 3.
    assert answers["a" * 40].tags == ["kafka", "messaging", "backend"]
    assert answers["a" * 40].quality == 1.0
    assert (answers["b" * 40].tags, answers["b" * 40].quality) == ([], 0.0)
    assert answers.keys() == {"a" * 40, "b" * 40}
    prompt = client.calls[0]["messages"][0]["content"]
    assert "- kafka: Kafka." in prompt  # taxonomy is in the prompt
    assert '"domain": "x.dev"' in prompt
    assert "a" * 9 not in prompt  # short ids only


def test_answer_without_quality_is_skipped() -> None:
    client = FakeClient(json.dumps({"items": [{"id": "a" * 8, "tags": ["kafka"]}]}))

    assert llm_tag_batch(chain(client), TAXONOMY, ITEMS[:1]) == {}


def test_every_provider_failing_raises() -> None:
    with pytest.raises(AIUnavailable):
        llm_tag_batch(chain(FakeClient("not json")), TAXONOMY, ITEMS)


def test_short_ids_grow_on_collision() -> None:
    items = [LLMTagItem(id="c" * 39 + "1", title="x"), LLMTagItem(id="c" * 39 + "2", title="y")]
    client = FakeClient(json.dumps({"items": []}))

    llm_tag_batch(chain(client), TAXONOMY, items)

    prompt = client.calls[0]["messages"][0]["content"]
    assert '"id": "' + "c" * 39 + "1" in prompt


def test_offline_fake_answers_the_tag_prompt(cfg: Config) -> None:
    answers = llm_tag_batch(fake_chain(cfg.ai), TAXONOMY, ITEMS)

    assert answers["a" * 40].tags == ["kafka"]  # its words are in the title
    assert len(answers["b" * 40].tags) == 1  # fallback: one valid slug
    assert all(t in TAXONOMY.by_slug for a in answers.values() for t in a.tags)
    assert all(0 <= a.quality <= 1 for a in answers.values())
    assert llm_tag_batch(fake_chain(cfg.ai), TAXONOMY, ITEMS) == answers  # deterministic
