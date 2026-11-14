"""Unit tests for the store's pydantic models and the sources.yaml loader (no database)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from digest.config import ConfigError
from digest.models import Item
from digest.store import FetchOutcome, NewItem, Stage, load_sources

REPO_ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 11, 15, 7, 0, tzinfo=UTC)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_repo_sources_yaml_loads() -> None:
    sources = load_sources(REPO_ROOT / "sources.yaml")
    specs = sources.specs()

    rss = [s for s in specs if s.kind == "rss"]
    devto = [s for s in specs if s.kind == "devto"]
    assert len(rss) == len(sources.rss) > 0
    assert len(devto) == len(sources.devto.tags) > 0
    assert len({s.key for s in specs}) == len(specs)
    assert all(s.cadence_hours == 24 for s in rss)
    assert all(s.cadence_hours == 3 for s in devto)


def test_specs_apply_defaults_and_overrides(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
defaults:
  rss: {cadence_hours: 12}
devto:
  tags: [java]
rss:
  - {name: A, url: "https://a.example/feed", category: ai, base_weight: 70}
  - {name: B, url: "https://b.example/feed", cadence_hours: 6}
""",
    )

    a, b, java = load_sources(path).specs()

    assert (a.kind, a.category, a.base_weight, a.cadence_hours) == ("rss", "ai", 70, 12)
    assert (b.base_weight, b.cadence_hours) == (50, 6)
    assert (java.kind, java.name, java.url) == ("devto", "dev.to #java", "https://dev.to/t/java")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("rss:\n  - {name: A, url: 'https://a/x', colour: red}\n", "rss.0.colour"),
        ("rss:\n  - {name: A, url: 'ftp://a/x'}\n", "rss.0.url"),
        ("rss:\n  - {name: A, url: 'https://a/x', base_weight: 101}\n", "rss.0.base_weight"),
        (
            "rss:\n  - {name: A, url: 'https://a/x'}\n  - {name: B, url: 'https://a/x'}\n",
            "duplicate rss url",
        ),
        ("devto: {tags: [go, go]}\n", "duplicate devto tag"),
        ("unknown: 1\n", "unknown"),
        ("- a\n", "must be a mapping"),
        ("rss: [\n", "Invalid YAML"),
    ],
)
def test_invalid_sources_file(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_sources(write(tmp_path, text))


def test_missing_sources_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_sources(tmp_path / "nope.yaml")


def test_new_item_from_pipeline_item() -> None:
    item = Item(
        id="c" * 40,
        source="hn",
        title="Title",
        url="https://example.com/a",
        discussion_url="https://news.ycombinator.com/item?id=1",
        score=120,
        comments=33,
        published_at=T0,
    )

    new = NewItem.from_item(item, source_id=7, fetched_at=T0)

    assert new.source_id == 7
    assert (new.native_score, new.native_comments) == (120, 33)
    assert new.discussion_url == item.discussion_url
    assert new.description == getattr(item, "description", None)


def test_new_item_requires_sha1_id_and_aware_datetimes() -> None:
    with pytest.raises(ValidationError):
        NewItem(id="x", source_id=1, title="t", url="u", published_at=T0, fetched_at=T0)
    with pytest.raises(ValidationError):
        NewItem(
            id="c" * 40,
            source_id=1,
            title="t",
            url="u",
            published_at=datetime(2026, 1, 1),
            fetched_at=T0,
        )


def test_fetch_outcome_error_iff_failed() -> None:
    FetchOutcome(at=T0, ok=True, etag='"x"')
    FetchOutcome(at=T0, ok=False, error="HTTP 500")
    with pytest.raises(ValidationError):
        FetchOutcome(at=T0, ok=False)
    with pytest.raises(ValidationError):
        FetchOutcome(at=T0, ok=True, error="oops")


def test_stage_columns() -> None:
    assert [s.column for s in Stage] == ["enriched_at", "tagged_at", "embedded_at", "summarized_at"]
