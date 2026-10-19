import json

from digest.ai.llm import LLMChain, Provider
from digest.config import Config
from digest.models import RadarEntry
from digest.radar.notes import summarize_releases
from fakes import FakeClient, rate_limited


def entry(
    key: str, kind: str = "major", notes: str | None = "## Breaking\n- dropped Python 3.8"
) -> RadarEntry:
    return RadarEntry(
        key=key,
        kind=kind,
        ecosystem="PyPI",
        package="httpx",
        installed="0.27.0",
        title=key,
        url=f"https://example.com/{key}",
        version="0.28.0",
        notes=notes,
    )


def chain(*replies) -> tuple[LLMChain, FakeClient]:
    client = FakeClient(*replies)
    return LLMChain([Provider("fake", "m", client)]), client


def cfg() -> Config:
    return Config.model_validate(
        {"profile": "Full-stack dev.", "radar": {"notes_max_chars": 20}, "ai": {"enabled": True}}
    )


def test_release_entries_get_summary_and_action_flag() -> None:
    reply = json.dumps(
        {
            "items": [
                {
                    "id": "encode/httpx@0.28.0",
                    "summary": "Bỏ hỗ trợ Python 3.8.",
                    "action_required": True,
                },
                {"id": "unknown", "summary": "x", "action_required": False},
            ]
        }
    )
    llm, client = chain(reply)
    entries = [entry("GHSA-1", kind="vulnerability"), entry("encode/httpx@0.28.0")]

    updated, completion = summarize_releases(llm, cfg(), entries)

    assert completion is not None and completion.provider == "fake"
    assert [(e.key, e.summary, e.action_required) for e in updated] == [
        ("GHSA-1", None, None),
        ("encode/httpx@0.28.0", "Bỏ hỗ trợ Python 3.8.", True),
    ]
    prompt = client.calls[0]["messages"][0]["content"]
    assert "Full-stack dev." in prompt and "Vietnamese" in prompt
    sent = json.loads(prompt.split("Releases:\n", 1)[1])
    assert sent == [
        {
            "id": "encode/httpx@0.28.0",
            "package": "httpx",
            "installed": "0.27.0",
            "release": "0.28.0",
            "major": True,
            "title": "encode/httpx@0.28.0",
            "notes": "## Breaking\n- droppe",  # truncated to notes_max_chars
        }
    ]


def test_no_release_entries_means_no_request() -> None:
    llm, client = chain()
    entries = [entry("GHSA-1", kind="vulnerability")]
    assert summarize_releases(llm, cfg(), entries) == (entries, None)
    assert client.calls == []


def test_ai_unavailable_leaves_entries_unchanged() -> None:
    llm, _ = chain(rate_limited())
    entries = [entry("o/r@v2")]
    assert summarize_releases(llm, cfg(), entries) == (entries, None)


def test_raw_notes_are_not_saved_in_the_digest_json() -> None:
    assert "notes" not in entry("o/r@v2").model_dump()
