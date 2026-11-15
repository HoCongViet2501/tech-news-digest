import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from digest.ai.llm import LLMChain, Provider
from digest.config import TaggingConfig
from digest.store import MemoryStore, NewItem, SourceSpec, StoredItem, TagMethod
from digest.tagger import Decision, TagVectors, decide, tag_pending
from digest.taxonomy import load_taxonomy
from fakes import FakeClient

REPO_ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 11, 15, 7, 0, tzinfo=UTC)
TAXONOMY = load_taxonomy(REPO_ROOT / "taxonomy.yaml")
CFG = TaggingConfig()


def item(title: str, description: str | None = None, **kw: object) -> StoredItem:
    data: dict[str, object] = {
        "id": "a" * 40,
        "source_id": 1,
        "title": title,
        "url": "https://example.com/x",
        "description": description,
        "published_at": T0,
        "fetched_at": T0,
        "first_seen_at": T0,
    }
    return StoredItem.model_validate(data | kw)


class OneHot:
    """Tag description i -> basis vector i, so item vectors pick exact similarities."""

    name = "one-hot"

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), 384), dtype=np.float32)
        out[np.arange(len(texts)), np.arange(len(texts))] = 1
        return out


VECTORS = TagVectors.build(TAXONOMY, OneHot())  # type: ignore[arg-type]
ROW = {slug: n for n, slug in enumerate(VECTORS.slugs)}


def vector(**sims: float) -> list[float]:
    out = [0.0] * 384
    for slug, sim in sims.items():
        out[ROW[slug.replace("_", "-")]] = sim
    return out


def run(it: StoredItem, vec: list[float] | None = None, cfg: TaggingConfig = CFG) -> Decision:
    return decide(it, vec, TAXONOMY, VECTORS, cfg)


def tags_of(decision: Decision) -> list[tuple[str, str]]:
    return [(t.tag, t.method.value) for t in decision.tags]


def test_native_tags_map_through_aliases() -> None:
    d = run(item("Some post", native_tags=["k8s", "golang", "unknown"]))

    assert tags_of(d) == [("golang", "native"), ("kubernetes", "native")]
    assert all(t.confidence == 0.95 for t in d.tags)
    assert not d.needs_llm  # no vector: nothing to judge "is it tech" by


def test_rules_match_title_and_description_with_boundaries() -> None:
    # A confident (title) tag drops the tentative description-only ones.
    d = run(item("Modern JavaScript patterns", "Notes on Kafka consumers."))

    assert [(t.tag, t.method.value, t.confidence) for t in d.tags] == [
        ("javascript", "rule", 0.9),
    ]
    tentative = run(item("Ten patterns", "Notes on Kafka consumers in modern JavaScript."))
    assert [(t.tag, t.confidence) for t in tentative.tags] == [
        ("javascript", 0.7),  # description only: tentative
        ("kafka", 0.7),
    ]
    assert "java" not in {t.tag for t in d.tags + tentative.tags}


def test_description_only_hits_are_not_confident() -> None:
    d = run(item("Ten things", "Available for Windows, macOS and Linux."), vector(devops=0.3))

    assert tags_of(d) == [("linux", "rule")]
    assert d.needs_llm


def test_native_beats_rule_for_the_same_tag() -> None:
    d = run(item("Kafka tips", native_tags=["kafka"]))

    assert tags_of(d) == [("kafka", "native")]


def test_embedding_step_only_without_a_confident_tag() -> None:
    v = vector(devops=0.7, security=0.55, ai=0.52, frontend=0.4)

    plain = run(item("An untitled post"), v)
    confident = run(item("Kafka at scale"), v)

    # Top embed_max_tags (2) above the threshold, mapped into the 0.6-0.75 band.
    assert tags_of(plain) == [("devops", "embed"), ("security", "embed")]
    assert all(0.6 <= t.confidence < CFG.confident for t in plain.tags)
    assert plain.tags[0].confidence > plain.tags[1].confidence
    assert plain.needs_llm  # nothing confident yet, and it looks like tech
    assert tags_of(confident) == [("kafka", "rule")]
    assert not confident.needs_llm


def test_language_tags_never_come_from_embeddings() -> None:
    v = vector(python=0.99, golang=0.99, languages=0.6)

    d = run(item("An untitled post"), v)

    assert tags_of(d) == [("languages", "embed")]


def test_per_tag_threshold_override() -> None:
    cfg = CFG.model_copy(update={"embed_thresholds": {"security": 0.6}})

    d = run(item("An untitled post"), vector(security=0.55), cfg)

    assert d.tags == []


@pytest.mark.parametrize(
    ("best", "quality", "needs"), [(0.2, None, False), (0.35, None, True), (0.35, 0.5, False)]
)
def test_llm_needs_tech_and_no_quality_yet(best: float, quality: float | None, needs: bool) -> None:
    d = run(item("An untitled post", quality=quality), vector(mobile=best))

    assert d.needs_llm is needs


def scripted_chain(*replies: str) -> LLMChain:
    return LLMChain([Provider("scripted", "m", FakeClient(*replies))])


def test_llm_answer_replaces_tentative_tags_and_sets_quality() -> None:
    store = MemoryStore()
    store.sync_sources([SourceSpec(kind="rss", name="A", url="https://a.example/feed")])
    store.sync_tags(TAXONOMY.specs())
    new = NewItem(
        id="a" * 40,
        source_id=store.list_sources()[0].id,
        title="Ten things",
        url="https://a.example/1",
        description="Available for Windows, macOS and Linux.",
        published_at=T0,
        fetched_at=T0,
    )
    store.upsert_items([new])
    store.save_embeddings({new.id: vector(mobile=0.4)}, T0)
    reply = json.dumps({"items": [{"id": "a" * 8, "tags": ["mobile", "bogus"], "quality": 0.3}]})

    stats = tag_pending(
        store,
        TAXONOMY,
        OneHot(),  # type: ignore[arg-type]
        CFG,
        now=T0,
        chain=scripted_chain(reply),
        llm_calls=5,
        source_names={},
    )

    assert (stats.tagged, stats.llm_calls, stats.llm_items, stats.llm_waiting) == (1, 1, 1, 0)
    assert [(t.tag, t.method) for t in store.get_item_tags([new.id])] == [("mobile", TagMethod.LLM)]
    [stored] = store.get_items([new.id])
    assert stored.quality == 0.3 and stored.tagged_at == T0
