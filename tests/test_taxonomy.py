from pathlib import Path

import pytest

from digest.config import ConfigError
from digest.taxonomy import LANGUAGE_ROOT, Taxonomy, keyword_regex, load_taxonomy

REPO_ROOT = Path(__file__).resolve().parents[1]


def tag(slug: str, parent: str | None = None, **kw: object) -> dict[str, object]:
    return {"slug": slug, "name": slug.title(), "parent": parent, "description": "d."} | kw


def test_real_taxonomy_loads() -> None:
    taxonomy = load_taxonomy(REPO_ROOT / "taxonomy.yaml")

    assert len(taxonomy.tags) >= 60
    assert taxonomy.alias_map["k8s"] == "kubernetes"
    assert taxonomy.alias_map["kubernetes"] == "kubernetes"  # the slug itself
    assert taxonomy.ancestors("kafka") == ["messaging", "backend"]
    embed = {t.slug for t in taxonomy.embed_tags}
    assert "devops" in embed and LANGUAGE_ROOT in embed
    assert not embed & {"python", "golang", "java", "spring", "rust", "typescript"}


@pytest.mark.parametrize(
    ("tags", "message"),
    [
        ([tag("a"), tag("a")], "duplicate slug: a"),
        ([tag("a", "b")], "unknown parent"),
        ([tag("a", "b"), tag("b", "a")], "parent cycle"),
        ([tag("a", keywords=["(unclosed"])], "not a valid regex"),
        ([tag("a", aliases=["Big Data"])], "aliases must be lowercase"),
        ([tag("a", aliases=["x"]), tag("b", aliases=["x"])], "maps to both"),
        ([tag(LANGUAGE_ROOT), tag("go", LANGUAGE_ROOT, embed=True)], "cannot use embeddings"),
        ([tag("Bad Slug")], "slug"),
    ],
)
def test_invalid_taxonomies_are_rejected(tags: list[dict[str, object]], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Taxonomy.model_validate({"tags": tags})


def test_load_taxonomy_reports_errors(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.yaml"
    path.write_text("tags:\n  - {slug: a, name: A, description: d, parent: zzz}\n")

    with pytest.raises(ConfigError, match="unknown parent"):
        load_taxonomy(path)
    with pytest.raises(ConfigError, match="not found"):
        load_taxonomy(tmp_path / "missing.yaml")


@pytest.mark.parametrize(
    ("pattern", "text", "hit"),
    [
        ("Java", "Java 25 is out", True),
        ("Java", "Modern JavaScript tips", False),
        ("SQL", "PostgreSQL 19 beta", False),
        ("SQL", "Writing faster SQL", True),
        (r"\.NET", "What's new in .NET 10", True),
        ("C#", "C# 14 features", True),
        ("Go 1\\.\\d+", "Go 1.26 released", True),
        ("Kubernetes", "kubernetes operators", True),  # case-insensitive by default
        ("(?-i:React)", "how users react to change", False),  # opt-out per keyword
        ("(?-i:React)", "React 20 compiler", True),
    ],
)
def test_keyword_regex_boundaries(pattern: str, text: str, hit: bool) -> None:
    assert bool(keyword_regex(pattern).search(text)) is hit
