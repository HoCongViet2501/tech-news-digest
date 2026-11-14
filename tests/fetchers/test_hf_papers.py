import asyncio
from datetime import UTC, datetime

import respx

from conftest import fixture_bytes
from digest.fetchers import hf_papers
from digest.http import make_client
from digest.models import Item

API = "https://huggingface.co/api/daily_papers"
NOW = datetime(2026, 11, 15, 1, 0, tzinfo=UTC)


def fetch() -> list[Item]:
    async def go() -> list[Item]:
        async with make_client() as client:
            return await hf_papers.fetch(client, now=NOW)

    return asyncio.run(go())


@respx.mock
def test_parses_fixture() -> None:
    respx.get(API).respond(200, content=fixture_bytes("hf_papers.json"))

    items = fetch()

    assert len(items) == 5
    first = items[0]
    assert first.source == "hf_papers"
    assert first.url == "https://huggingface.co/papers/2611.10439"
    assert first.title.startswith("Who Remembers What?")
    assert first.score == 7  # upvotes
    assert first.comments == 3
    assert first.published_at == datetime(2026, 11, 13, tzinfo=UTC)  # submittedOnDailyAt
    assert "CrossLearnBench" in first.tags  # ai_keywords
    assert first.description is not None and first.description.startswith("MixUA distills")


@respx.mock
def test_long_abstract_is_trimmed_when_ai_summary_is_missing() -> None:
    respx.get(API).respond(200, content=fixture_bytes("hf_papers.json"))

    no_keywords = fetch()[2]

    assert no_keywords.tags == []
    assert no_keywords.description is not None
    assert len(no_keywords.description) <= 300


@respx.mock
def test_error_returns_empty() -> None:
    respx.get(API).respond(429)

    assert fetch() == []
