"""Load and validate taxonomy.yaml, the shared tag list (docs/PLAN.md §15 Tagging).

The file is synced into the `tags` table at the start of every ingest run; the tagger
uses the aliases (step 1), keyword regexes (step 2) and, for `embed: true` tags, the
description (step 3).
"""

import re
from functools import cached_property
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from digest.config import ConfigError, _format_errors
from digest.store.models import TagSpec, normalize_native_tags

# Language tags (descendants of this one) never come from embeddings: the §11 experiment
# showed zero-shot similarity is wrong for Go, Python, etc. The root itself is a broad topic.
LANGUAGE_ROOT = "languages"


def keyword_regex(pattern: str) -> re.Pattern[str]:
    """Case-insensitive, bounded so that 'Java' never matches inside 'JavaScript' and
    'SQL' never inside 'PostgreSQL'. Lookarounds rather than \\b, so patterns that start or
    end with punctuation ('.NET', 'C#') still work. A pattern can opt out of the
    case-insensitivity with an inline group, e.g. '(?-i:React)'."""
    return re.compile(rf"(?<!\w)(?:{pattern})(?!\w)", re.IGNORECASE)


class TaxonomyTag(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1)
    parent: str | None = None
    description: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    embed: bool = False

    @field_validator("aliases")
    @classmethod
    def _normalized_aliases(cls, aliases: list[str]) -> list[str]:
        bad = [a for a in aliases if normalize_native_tags([a]) != [a]]
        if bad:
            raise ValueError(f"aliases must be lowercase with '-' for spaces: {bad}")
        return aliases

    @field_validator("keywords")
    @classmethod
    def _keywords_compile(cls, keywords: list[str]) -> list[str]:
        for pattern in keywords:
            try:
                keyword_regex(pattern)
            except re.error as exc:
                raise ValueError(f"keyword {pattern!r} is not a valid regex: {exc}") from exc
        return keywords

    def spec(self) -> TagSpec:
        return TagSpec(
            slug=self.slug,
            name=self.name,
            parent=self.parent,
            description=self.description,
            aliases=self.aliases,
        )


class Taxonomy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tags: list[TaxonomyTag] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        slugs = [t.slug for t in self.tags]
        dupes = sorted({s for s in slugs if slugs.count(s) > 1})
        if dupes:
            raise ValueError(f"duplicate slug: {', '.join(dupes)}")
        known = set(slugs)
        for tag in self.tags:
            if tag.parent is not None and tag.parent not in known:
                raise ValueError(f"{tag.slug}: unknown parent {tag.parent!r}")
        parents = {t.slug: t.parent for t in self.tags}
        for tag in self.tags:
            seen = {tag.slug}
            parent = tag.parent
            while parent is not None:
                if parent in seen:
                    raise ValueError(f"{tag.slug}: parent cycle through {parent!r}")
                seen.add(parent)
                parent = parents[parent]
        owner: dict[str, str] = {}
        for tag in self.tags:
            for alias in {tag.slug, *tag.aliases}:
                other = owner.setdefault(alias, tag.slug)
                if other != tag.slug:
                    raise ValueError(f"alias {alias!r} maps to both {other} and {tag.slug}")
        for tag in self.tags:
            if tag.embed and LANGUAGE_ROOT in self.ancestors(tag.slug):
                raise ValueError(f"{tag.slug}: language tags cannot use embeddings")
        return self

    def ancestors(self, slug: str) -> list[str]:
        """Parent, grandparent, ... of a tag (validation guarantees no cycles)."""
        parents = {t.slug: t.parent for t in self.tags}
        out = []
        parent = parents.get(slug)
        while parent is not None:
            out.append(parent)
            parent = parents[parent]
        return out

    @cached_property
    def by_slug(self) -> dict[str, TaxonomyTag]:
        return {t.slug: t for t in self.tags}

    @cached_property
    def alias_map(self) -> dict[str, str]:
        """Normalized native tag -> slug. A tag's own slug counts as an alias."""
        return {alias: t.slug for t in self.tags for alias in (t.slug, *t.aliases)}

    @cached_property
    def keyword_patterns(self) -> list[tuple[str, re.Pattern[str]]]:
        return [(t.slug, keyword_regex(k)) for t in self.tags for k in t.keywords]

    @property
    def embed_tags(self) -> list[TaxonomyTag]:
        return [t for t in self.tags if t.embed]

    def specs(self) -> list[TagSpec]:
        return [t.spec() for t in self.tags]


def load_taxonomy(path: Path) -> Taxonomy:
    if not path.is_file():
        raise ConfigError(f"Taxonomy file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Top level of {path} must be a mapping")
    try:
        return Taxonomy.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid taxonomy in {path}:\n{_format_errors(exc)}") from exc
