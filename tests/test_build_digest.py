import hashlib
import json
from datetime import UTC, date, datetime

import pytest

from digest.__main__ import build_digest
from digest.ai.llm import LLMChain, Provider
from digest.config import Config
from digest.models import Item
from fakes import FakeClient, rate_limited

DAY = date(2026, 9, 27)


def sha1_of(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def hn(n: int) -> Item:
    return Item(
        id=f"{n:03d}" + "b" * 37,
        source="hn",
        title=f"Story {n}",
        url=f"https://e{n}.example/p",
        score=100 + n,  # higher n = higher heuristic rank
        published_at=datetime(2026, 9, 27, tzinfo=UTC),
    )


FETCHED = {"hn": [hn(n) for n in range(1, 81)]}


@pytest.fixture
def cfg(cfg: Config) -> Config:
    cfg.max_items = 3
    cfg.ai.candidates = 60
    return cfg


def test_ai_disabled_uses_heuristic_only(cfg: Config) -> None:
    digest = build_digest(cfg, FETCHED, {}, DAY, chain=None)

    assert [i.title for i in digest.items] == ["Story 80", "Story 79", "Story 78"]
    assert "ai" not in digest.stats


def test_ai_scores_widened_candidate_list(cfg: Config) -> None:
    cfg.ai.enabled = True
    # The AI scores all 60 candidates (stories 21-80) and prefers low-heuristic 21 and 22.
    special = {21: 9, 22: 8}
    answer = {
        "items": [
            # normalize() re-derives ids as sha1(canonical URL); the scorer sends 8 chars
            {
                "id": sha1_of(f"https://e{n}.example/p")[:8],
                "score": special.get(n, 1),
                "reason": "r",
            }
            for n in range(21, 81)
        ]
    }
    client = FakeClient(json.dumps(answer))

    digest = build_digest(cfg, FETCHED, {}, DAY, chain=LLMChain([Provider("groq", "m", client)]))

    prompt = client.calls[0]["messages"][-1]["content"]
    assert len(json.loads(prompt.split("Items:\n", 1)[1])) == 60
    # the rest tie at 1 and are ordered by heuristic, so Story 80 comes next
    assert [i.title for i in digest.items] == ["Story 21", "Story 22", "Story 80"]
    assert digest.stats["ai"] == "groq"
    assert digest.stats["ai_tokens_in"] == 100
    assert digest.stats["ai_tokens_out"] == 20


@pytest.mark.parametrize(
    "chain",
    [LLMChain([]), LLMChain([Provider("p", "m", FakeClient(rate_limited()))])],
    ids=["no-keys", "provider-fails"],
)
def test_ai_unavailable_falls_back_to_heuristic(cfg: Config, chain: LLMChain) -> None:
    cfg.ai.enabled = True

    digest = build_digest(cfg, FETCHED, {}, DAY, chain=chain)

    assert [i.title for i in digest.items] == ["Story 80", "Story 79", "Story 78"]
    assert digest.stats["ai"] == "unavailable"
    assert all(i.reason is None for i in digest.items)
