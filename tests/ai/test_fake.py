import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from digest.__main__ import main
from digest.ai.fake import PREFIX, PROVIDER_NAME, FakeLLMClient, fake_chain
from digest.ai.llm import parse_json
from digest.ai.scorer import _ScoreResponse, score_items
from digest.ai.summarizer import _SummaryResponse, summarize_items
from digest.config import Config
from digest.models import ScoredItem

REPO_CONFIG = Path(__file__).parents[2] / "config.yaml"
KEY_ENV = ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY")


def scored(n: int) -> ScoredItem:
    return ScoredItem(
        id=f"{n:02d}" + "b" * 38,
        source="hn",
        title=f"Title {n}",
        url=f"https://site{n}.example/post",
        score=100 + n,
        published_at=datetime(2026, 9, 27, tzinfo=UTC),
        relevance=float(n),
    )


def test_fake_score_reply_parses_with_real_parser(cfg: Config) -> None:
    chain = fake_chain(cfg.ai)
    items = [scored(n) for n in range(1, 6)]

    ranked, completion = score_items(chain, cfg, items)

    assert completion.provider == PROVIDER_NAME
    assert len(completion.data.items) == 5
    assert all(0 <= s.score <= 10 for s in completion.data.items)
    assert all(item.reason and item.reason.startswith(PREFIX) for item in ranked)


def test_fake_summary_reply_parses_with_real_parser(cfg: Config) -> None:
    chain = fake_chain(cfg.ai)
    items = [scored(n) for n in range(1, 4)]
    contexts = {items[0].id: "some text", items[2].id: "more text"}

    out, completions = summarize_items(chain, cfg, items, contexts)

    assert len(completions) == 1
    assert out[0].summary and out[0].summary.startswith(PREFIX)
    assert out[0].why_it_matters and out[0].why_it_matters.startswith(PREFIX)
    assert "Title 1" in out[0].summary
    assert out[1].summary is None  # no context, nothing invented
    assert out[2].summary and "Title 3" in out[2].summary


def test_fake_replies_are_deterministic_and_valid_json(cfg: Config) -> None:
    from digest.ai.prompts import load_prompt

    items_json = json.dumps([{"id": "abc", "title": "T", "source": "hn", "content": "x"}])
    score = load_prompt("score", profile="p", output_language="Vietnamese", items_json=items_json)
    summ = load_prompt(
        "summarize", profile="p", output_language="Vietnamese", items_json=items_json
    )

    a, b = FakeLLMClient("vi"), FakeLLMClient("vi")
    assert a.reply(score) == b.reply(score)
    assert parse_json(a.reply(score), _ScoreResponse).items[0].id == "abc"
    assert parse_json(a.reply(summ), _SummaryResponse).items[0].summary.startswith(PREFIX)
    assert FakeLLMClient("en").reply(summ) != a.reply(summ)  # follows output_language


def test_fake_rejects_unknown_prompt() -> None:
    with pytest.raises(ValueError):
        parse_json(FakeLLMClient().reply("something else entirely"), _ScoreResponse)


def offline_dry_run(tmp_path: Path) -> int:
    return main(
        [
            "run",
            "--dry-run",
            "--date",
            "2026-09-27",
            "--config",
            str(REPO_CONFIG),
            "--data-dir",
            str(tmp_path / "data"),
            "--site-dir",
            str(tmp_path / "site"),
        ]
    )


def test_offline_run_never_calls_providers_even_with_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    for var in KEY_ENV:
        monkeypatch.setenv(var, f"fake-{var.lower()}")
    attempts: list[str] = []

    def refuse(self, request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        raise AssertionError(f"real network request to {request.url}")

    async def refuse_async(self, request: httpx.Request) -> httpx.Response:
        return refuse(self, request)

    # Every real (non-mock) httpx transport, sync (openai SDK) and async, refuses to send.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse_async)
    caplog.set_level("INFO", logger="digest")

    assert offline_dry_run(tmp_path) == 0

    out = capsys.readouterr().out
    assert attempts == []
    assert f"ai={PROVIDER_NAME}" in caplog.text
    assert "fake provider" in caplog.text
    assert f"{PREFIX} Tóm tắt giả lập" in out
    assert f"💡 {PREFIX}" in out
    for var in KEY_ENV:
        assert f"fake-{var.lower()}" not in caplog.text


def test_two_offline_dry_runs_are_identical(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert offline_dry_run(tmp_path / "a") == 0
    first = capsys.readouterr().out
    assert offline_dry_run(tmp_path / "b") == 0
    second = capsys.readouterr().out

    assert first == second
    assert PREFIX in first
