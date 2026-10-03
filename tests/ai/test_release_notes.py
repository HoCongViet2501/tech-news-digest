import json
from datetime import date

from digest.__main__ import add_radar
from digest.ai.llm import LLMChain, Provider
from digest.ai.release_notes import summarize_releases
from digest.config import Config
from digest.models import Digest, RadarEntry
from digest.radar.run import RadarResult
from fakes import FakeClient, rate_limited


def entry(kind: str, n: int, notes: str | None = "## Removed\n* proxies argument") -> RadarEntry:
    return RadarEntry(
        id=f"release:o/r{n}@2.0.0" if kind != "vulnerability" else f"vuln:GHSA-{n}:npm:x",
        kind=kind,
        ecosystem="PyPI",
        package=f"pkg{n}",
        installed="1.4.0",
        version="2.0.0",
        title="2.0.0",
        url="https://github.com/o/r/releases/tag/2.0.0",
        notes=notes,
    )


def chain(*replies) -> tuple[LLMChain, FakeClient]:
    client = FakeClient(*replies)
    return LLMChain([Provider("gemini", "m", client)]), client


def test_releases_with_notes_get_summary_and_action(cfg: Config) -> None:
    entries = [entry("vulnerability", 1), entry("major", 2), entry("breaking", 3, notes=None)]
    llm, client = chain(
        json.dumps({"items": [{"id": "r2", "summary": "Bỏ proxies.", "action_required": True}]})
    )

    out, completions = summarize_releases(llm, cfg, entries)

    prompt = client.calls[0]["messages"][0]["content"]
    sent = json.loads(prompt.split("Releases:\n", 1)[1])
    assert sent == [
        {
            "id": "r2",
            "package": "pkg2",
            "installed": "1.4.0",
            "release": "2.0.0",
            "notes": "## Removed\n* proxies argument",
        }
    ]
    assert "Vietnamese" in prompt
    assert out[1].summary == "Bỏ proxies."
    assert out[1].action_required is True
    assert out[0].summary is None and out[2].summary is None
    assert len(completions) == 1


def test_nothing_to_summarize_makes_no_request(cfg: Config) -> None:
    llm, client = chain()

    out, completions = summarize_releases(llm, cfg, [entry("vulnerability", 1)])

    assert client.calls == []
    assert completions == []
    assert out[0].summary is None


def test_ai_failure_leaves_entries_unchanged(cfg: Config) -> None:
    llm, _ = chain(rate_limited())
    entries = [entry("major", 1)]

    out, completions = summarize_releases(llm, cfg, entries)

    assert out == entries
    assert completions == []


def test_add_radar_skips_ai_when_it_was_unavailable(cfg: Config) -> None:
    llm, client = chain()
    digest = Digest(date=date(2026, 9, 27), items=[], stats={"ai": "unavailable"})
    result = RadarResult(entries=[entry("major", 1)], repo_map={}, dependencies=3)

    out = add_radar(cfg, digest, result, llm)

    assert client.calls == []
    assert out.radar == result.entries
    assert out.stats["radar"] == 1
    assert out.stats["radar_dependencies"] == 3


def test_add_radar_adds_ai_tokens(cfg: Config) -> None:
    llm, _ = chain(json.dumps({"items": [{"id": "r1", "summary": "S", "action_required": False}]}))
    digest = Digest(date=date(2026, 9, 27), items=[], stats={"ai": "gemini", "ai_tokens_in": 50})
    result = RadarResult(entries=[entry("major", 1)], repo_map={}, dependencies=1)

    out = add_radar(cfg, digest, result, llm)

    assert out.radar[0].action_required is False
    assert out.stats["ai_tokens_in"] == 150
    assert out.stats["ai_tokens_out"] == 20
