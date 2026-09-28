"""Summaries and "why it matters" for the final items.

Context per item: the extracted article text; if that fails (paywall, JS-only
page), the top 3 HN comments or the GitHub repo description. Items with no
context are not summarized, so nothing is invented.
"""

import html
import json
import logging
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import trafilatura
from pydantic import BaseModel

from digest.ai.llm import AIUnavailable, Completion, LLMChain
from digest.ai.prompts import language_name, load_prompt
from digest.config import Config
from digest.models import ScoredItem

ARTICLE_TIMEOUT = 10
HN_ITEM_API = "https://hn.algolia.com/api/v1/items/{id}"
REPO_API = "https://api.github.com/repos/{name}"
TOP_COMMENTS = 3
TAG = re.compile(r"<[^>]+>")

log = logging.getLogger(__name__)


class _Summary(BaseModel):
    id: str
    summary: str
    why_it_matters: str | None = None


class _SummaryResponse(BaseModel):
    items: list[_Summary]


def _html_to_text(fragment: str) -> str:
    return " ".join(html.unescape(TAG.sub(" ", fragment)).split())


async def _article(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        response = await client.get(url, timeout=ARTICLE_TIMEOUT)
        response.raise_for_status()
        return trafilatura.extract(response.text) or None
    except Exception as exc:  # any failure just means "try the fallback"
        log.info("summarizer: no article text for %s (%s)", url, type(exc).__name__)
        return None


async def _hn_comments(client: httpx.AsyncClient, discussion_url: str) -> str | None:
    ids = parse_qs(urlsplit(discussion_url).query).get("id")
    if not ids:
        return None
    try:
        response = await client.get(HN_ITEM_API.format(id=ids[0]), timeout=ARTICLE_TIMEOUT)
        response.raise_for_status()
        children = response.json().get("children") or []
    except Exception as exc:
        log.info("summarizer: no HN comments for %s (%s)", ids[0], type(exc).__name__)
        return None
    texts = [_html_to_text(c["text"]) for c in children if c.get("text")][:TOP_COMMENTS]
    return "\n\n".join(texts) or None


async def _repo_description(client: httpx.AsyncClient, url: str) -> str | None:
    name = urlsplit(url).path.strip("/")
    try:
        response = await client.get(REPO_API.format(name=name), timeout=ARTICLE_TIMEOUT)
        response.raise_for_status()
        return response.json().get("description") or None
    except Exception as exc:
        log.info("summarizer: no repo description for %s (%s)", name, type(exc).__name__)
        return None


async def gather_context(client: httpx.AsyncClient, item: ScoredItem, max_chars: int) -> str | None:
    text = await _article(client, item.url)
    if not text and item.discussion_url and "news.ycombinator.com" in item.discussion_url:
        text = await _hn_comments(client, item.discussion_url)
    if not text and urlsplit(item.url).hostname == "github.com":
        text = await _repo_description(client, item.url)
    return text[:max_chars] if text else None


def summarize_items(
    chain: LLMChain, cfg: Config, items: list[ScoredItem], contexts: dict[str, str]
) -> tuple[list[ScoredItem], list[Completion[_SummaryResponse]]]:
    """Fill summary/why_it_matters in batches; a failed batch leaves its items as they are."""
    with_context = [item for item in items if contexts.get(item.id)]
    size = cfg.ai.summary_batch_size
    results: dict[str, _Summary] = {}
    completions = []
    for start in range(0, len(with_context), size):
        batch = with_context[start : start + size]
        items_json = json.dumps(
            [
                {"id": i.id, "title": i.title, "source": i.source, "content": contexts[i.id]}
                for i in batch
            ],
            ensure_ascii=False,
        )
        prompt = load_prompt(
            "summarize",
            profile=cfg.profile.strip(),
            output_language=language_name(cfg.ai.output_language),
            items_json=items_json,
        )
        try:
            completion = chain.complete([{"role": "user", "content": prompt}], _SummaryResponse)
        except AIUnavailable:
            log.warning("summarizer: batch of %d left without summaries", len(batch))
            continue
        completions.append(completion)
        wanted = {i.id for i in batch}
        results.update({s.id: s for s in completion.data.items if s.id in wanted})
    out = [
        item.model_copy(
            update={
                "summary": results[item.id].summary,
                "why_it_matters": results[item.id].why_it_matters,
            }
        )
        if item.id in results
        else item
        for item in items
    ]
    return out, completions
