import json
from datetime import UTC, date, datetime

from digest.__main__ import _profile_suggestions
from digest.ai.llm import LLMChain, Provider
from digest.config import Config
from digest.feedback.weekly import build_weekly
from digest.models import Vote
from digest.render import render_weekly
from fakes import FakeClient, rate_limited

END = date(2026, 9, 27)


def votes(n: int) -> list[Vote]:
    return [
        Vote(
            ts=datetime(2026, 9, 26, tzinfo=UTC),
            update_id=i,
            item_id=f"item{i}",
            vote="up" if i % 2 else "down",
            title=f"Kubernetes story {i}" if i % 2 else f"Crypto story {i}",
            source="hn",
        )
        for i in range(1, n + 1)
    ]


def chain(*replies) -> tuple[LLMChain, FakeClient]:
    client = FakeClient(*replies)
    return LLMChain([Provider("gemini", "m", client)]), client


SUGGESTIONS = json.dumps(
    {
        "suggestions": [
            {"change": "Thêm Kubernetes operators", "why": "Bạn thích 3 tin về Kubernetes"},
            {"change": "Bỏ crypto", "why": "Bạn không thích tin crypto"},
            {"change": "Thừa", "why": "x"},
            {"change": "Thừa nữa", "why": "y"},
        ]
    }
)


def test_suggestions_from_recent_votes(cfg: Config) -> None:
    cfg.ai.enabled = True
    llm, client = chain(SUGGESTIONS)

    suggestions = _profile_suggestions(cfg, votes(6), END, llm)

    prompt = client.calls[0]["messages"][0]["content"]
    assert "<profile>Backend developer.</profile>" in prompt
    assert "at most 3 concrete changes" in prompt
    assert '"title": "Kubernetes story 5"' in prompt
    assert [s.change for s in suggestions] == ["Thêm Kubernetes operators", "Bỏ crypto", "Thừa"]


def test_too_few_votes_or_ai_disabled_means_no_request(cfg: Config) -> None:
    llm, client = chain()

    assert _profile_suggestions(cfg, votes(10), END, llm) == []  # AI disabled
    cfg.ai.enabled = True
    assert _profile_suggestions(cfg, votes(4), END, llm) == []  # below min votes
    cfg.weekly.max_profile_suggestions = 0
    assert _profile_suggestions(cfg, votes(10), END, llm) == []
    assert client.calls == []


def test_ai_failure_means_no_suggestions(cfg: Config) -> None:
    cfg.ai.enabled = True
    llm, _ = chain(rate_limited())

    assert _profile_suggestions(cfg, votes(6), END, llm) == []


def test_suggestions_are_rendered(cfg: Config) -> None:
    cfg.ai.enabled = True
    llm, _ = chain(SUGGESTIONS)
    report = build_weekly([], [], END, top_items=5)
    report.suggestions = _profile_suggestions(cfg, votes(6), END, llm)

    text = render_weekly(report, "vi")

    assert "<b>💡 Gợi ý chỉnh profile</b>" in text
    assert "• Thêm Kubernetes operators <i>(Bạn thích 3 tin về Kubernetes)</i>" in text
