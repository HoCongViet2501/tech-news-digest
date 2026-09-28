import asyncio
import json
from datetime import UTC, datetime

import pytest
import respx

from conftest import fixture_bytes
from digest.ai.llm import LLMChain, Provider
from digest.ai.summarizer import gather_context, summarize_items
from digest.config import Config
from digest.http import make_client
from digest.models import ScoredItem
from fakes import FakeClient, rate_limited

ARTICLE = "https://lwn.net/Articles/1096897"
HN_ITEM_API = "https://hn.algolia.com/api/v1/items/49868083"
REPO_API = "https://api.github.com/repos/vectorize-io/hindsight"


def scored(n: int = 1, **kw) -> ScoredItem:
    base = dict(
        id=f"id{n}",
        source="rss:lwn",
        title=f"Title {n}",
        url=f"https://example.com/{n}",
        score=10,
        published_at=datetime(2026, 9, 27, tzinfo=UTC),
        relevance=5.0,
    )
    return ScoredItem(**(base | kw))


def context(item: ScoredItem, max_chars: int = 4000) -> str | None:
    async def go():
        async with make_client() as client:
            return await gather_context(client, item, max_chars)

    return asyncio.run(go())


# --- gather_context --------------------------------------------------------------


@respx.mock
def test_article_text_is_extracted_and_truncated() -> None:
    respx.get(ARTICLE).respond(200, content=fixture_bytes("article_lwn.html"))

    text = context(scored(url=ARTICLE), max_chars=4000)

    assert text is not None
    assert text.startswith("Version 18.1 of the GDB interactive debugger has been released.")
    assert len(text) <= 4000


@respx.mock
def test_hn_item_falls_back_to_top_three_comments() -> None:
    url = "https://eoinhiggins.substack.com/p/there-are-no-rogue-ai-agents"
    respx.get(url).respond(403)
    respx.get(HN_ITEM_API).respond(200, content=fixture_bytes("hn_item.json"))
    item = scored(
        source="hn", url=url, discussion_url="https://news.ycombinator.com/item?id=49868083"
    )

    text = context(item)

    assert text is not None
    assert "they had physically disconnected the sandbox" in text  # comment 1
    assert 'Should we put "functional" in front' in text  # comment 2, entities decoded
    assert "At worst, OpenAI knew about these behaviors" in text  # comment 3
    assert "pure immortal soul" not in text  # comment 4 is not used
    assert "<p>" not in text and "&quot;" not in text


@respx.mock
def test_js_only_page_falls_back_to_github_description() -> None:
    url = "https://github.com/vectorize-io/hindsight"
    respx.get(url).respond(200, content=b"<html><body><div id='root'></div></body></html>")
    respx.get(REPO_API).respond(200, content=fixture_bytes("github_repo.json"))

    text = context(scored(source="github", url=url))

    assert text == "Hindsight: Agent Memory That Learns"


@respx.mock
def test_no_context_available_returns_none() -> None:
    respx.get("https://example.com/1").respond(500)

    assert context(scored()) is None


# --- summarize_items -------------------------------------------------------------


def answer(ids: list[str]) -> str:
    return json.dumps(
        {"items": [{"id": i, "summary": f"S {i}", "why_it_matters": f"W {i}"} for i in ids]}
    )


@pytest.fixture
def cfg(cfg: Config) -> Config:
    cfg.profile = "Go backend developer."
    return cfg


def test_batches_of_five_and_fills_fields(cfg: Config) -> None:
    items = [scored(n) for n in range(1, 8)]
    contexts = {f"id{n}": f"body {n}" for n in range(1, 8)}
    client = FakeClient(
        answer([f"id{n}" for n in range(1, 6)]), answer([f"id{n}" for n in range(6, 8)])
    )

    out, completions = summarize_items(LLMChain([Provider("p", "m", client)]), cfg, items, contexts)

    assert len(client.calls) == 2
    first = json.loads(client.calls[0]["messages"][-1]["content"].split("Items:\n", 1)[1])
    assert [x["id"] for x in first] == ["id1", "id2", "id3", "id4", "id5"]
    assert first[0] == {"id": "id1", "title": "Title 1", "source": "rss:lwn", "content": "body 1"}
    assert [(i.summary, i.why_it_matters) for i in out][:2] == [
        ("S id1", "W id1"),
        ("S id2", "W id2"),
    ]
    assert out[6].summary == "S id7"
    assert [c.provider for c in completions] == ["p", "p"]


def test_prompt_has_profile_and_language(cfg: Config) -> None:
    client = FakeClient(answer(["id1"]))

    summarize_items(LLMChain([Provider("p", "m", client)]), cfg, [scored(1)], {"id1": "x"})

    prompt = client.calls[0]["messages"][-1]["content"]
    assert "<profile>Go backend developer.</profile>" in prompt
    assert "in Vietnamese" in prompt


def test_items_without_context_are_not_sent_or_summarized(cfg: Config) -> None:
    client = FakeClient(answer(["id1"]))

    out, _ = summarize_items(
        LLMChain([Provider("p", "m", client)]), cfg, [scored(1), scored(2)], {"id1": "x"}
    )

    sent = json.loads(client.calls[0]["messages"][-1]["content"].split("Items:\n", 1)[1])
    assert [x["id"] for x in sent] == ["id1"]
    assert out[1].summary is None
    assert [i.id for i in out] == ["id1", "id2"]  # order and membership unchanged


def test_failed_batch_leaves_items_unsummarized_and_continues(cfg: Config) -> None:
    items = [scored(n) for n in range(1, 7)]
    contexts = {f"id{n}": "x" for n in range(1, 7)}
    client = FakeClient(rate_limited(), answer(["id6", "unknown"]))

    out, completions = summarize_items(LLMChain([Provider("p", "m", client)]), cfg, items, contexts)

    assert [i.summary for i in out] == [None, None, None, None, None, "S id6"]
    assert len(completions) == 1
