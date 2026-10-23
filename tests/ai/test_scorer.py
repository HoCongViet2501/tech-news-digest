import json
from datetime import UTC, datetime

import pytest

from digest.ai.llm import AIUnavailable, LLMChain, Provider
from digest.ai.scorer import score_items
from digest.config import Config
from digest.models import ScoredItem
from fakes import FakeClient, rate_limited


def cand(n: int, relevance: float, source: str = "hn") -> ScoredItem:
    return ScoredItem(
        id=f"{n:02d}" + "a" * 38,
        source=source,
        title=f"Title {n}",
        url=f"https://www.site{n}.example/post",
        score=100 + n,
        published_at=datetime(2026, 9, 27, tzinfo=UTC),
        relevance=relevance,
    )


def reply(*entries: tuple[int, float, str]) -> str:
    return json.dumps(
        {"items": [{"id": f"{n:02d}aaaaaa", "score": s, "reason": r} for n, s, r in entries]}
    )


@pytest.fixture
def cfg(cfg: Config) -> Config:
    cfg.profile = "Go backend developer."
    cfg.max_items = 3
    return cfg


def test_prompt_contains_profile_language_and_compact_items(cfg: Config) -> None:
    client = FakeClient(reply((1, 5, "ok")))

    score_items(LLMChain([Provider("p", "m", client)]), cfg, [cand(1, 4.0)])

    prompt = client.calls[0]["messages"][-1]["content"]
    assert "<profile>Go backend developer.</profile>" in prompt
    assert "in Vietnamese" in prompt
    items = json.loads(prompt.split("Items:\n", 1)[1])
    assert items == [
        {
            "id": "01aaaaaa",
            "title": "Title 1",
            "source": "hn",
            "score": 101,
            "domain": "site1.example",
        }
    ]


def test_orders_by_ai_score_and_keeps_top_max_items(cfg: Config) -> None:
    client = FakeClient(reply((1, 2, "r1"), (2, 9, "r2"), (3, 7, "r3"), (4, 8, "r4")))

    items, completion = score_items(
        LLMChain([Provider("p", "m", client)]),
        cfg,
        [cand(1, 9.0), cand(2, 1.0), cand(3, 5.0), cand(4, 5.0)],
    )

    assert [(i.title, i.relevance, i.reason) for i in items] == [
        ("Title 2", 9.0, "r2"),
        ("Title 4", 8.0, "r4"),
        ("Title 3", 7.0, "r3"),
    ]
    assert completion.provider == "p"


def test_unknown_ids_ignored_and_missing_items_keep_heuristic(cfg: Config) -> None:
    client = FakeClient(
        json.dumps(
            {
                "items": [
                    {"id": "01aaaaaa", "score": 6, "reason": "r1"},
                    {"id": "zzzzzzzz", "score": 10, "reason": "hallucinated"},
                ]
            }
        )
    )

    items, _ = score_items(
        LLMChain([Provider("p", "m", client)]), cfg, [cand(1, 3.0), cand(2, 12.5)]
    )

    # item 2 was not scored: heuristic 12.5 is clamped to the 0-10 scale, no reason
    assert [(i.title, i.relevance, i.reason) for i in items] == [
        ("Title 2", 10.0, None),
        ("Title 1", 6.0, "r1"),
    ]


def test_ties_break_on_heuristic(cfg: Config) -> None:
    client = FakeClient(reply((1, 7, "a"), (2, 7, "b")))

    items, _ = score_items(
        LLMChain([Provider("p", "m", client)]), cfg, [cand(1, 2.0), cand(2, 6.0)]
    )

    assert [i.title for i in items] == ["Title 2", "Title 1"]


def test_out_of_range_scores_are_clamped(cfg: Config) -> None:
    client = FakeClient(reply((1, 15, "a"), (2, -3, "b")))

    items, _ = score_items(
        LLMChain([Provider("p", "m", client)]), cfg, [cand(1, 1.0), cand(2, 1.0)]
    )

    assert [i.relevance for i in items] == [10.0, 0.0]


def test_all_providers_failing_propagates(cfg: Config) -> None:
    with pytest.raises(AIUnavailable):
        score_items(LLMChain([Provider("p", "m", FakeClient(rate_limited()))]), cfg, [cand(1, 1.0)])


def test_prompt_includes_feedback_examples(cfg: Config) -> None:
    client = FakeClient(reply((1, 5, "ok")))

    score_items(
        LLMChain([Provider("p", "m", client)]),
        cfg,
        [cand(1, 4.0)],
        liked=["Postgres 19 released"],
        disliked=["Crypto drama"],
    )

    prompt = client.calls[0]["messages"][-1]["content"]
    assert "Liked:\n- Postgres 19 released\nDisliked:\n- Crypto drama\n" in prompt


def test_prompt_without_feedback_has_no_section(cfg: Config) -> None:
    client = FakeClient(reply((1, 5, "ok")))

    score_items(LLMChain([Provider("p", "m", client)]), cfg, [cand(1, 4.0)])

    prompt = client.calls[0]["messages"][-1]["content"]
    assert "Liked" not in prompt
    assert "{feedback}" not in prompt
    assert "marketing\n\nRules:" in prompt
