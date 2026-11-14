"""Load and validate sources.yaml (Part 2 ingestion sources) into SourceSpecs."""

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from digest.config import ConfigError, _format_errors
from digest.store.models import SourceSpec

DEVTO_TAG_URL = "https://dev.to/t/{tag}"

ApiKind = Literal[
    "hackernews",
    "lobsters",
    "lemmy",
    "viblo",
    "github_search",
    "hf_papers",
    "github_trending",
    "producthunt",
]

# Display name and URL of each API source. The URL is the (kind, url) natural key and
# its host is what the per-host fetch limit counts against, so it names the endpoint.
API_SOURCES: dict[str, tuple[str, str]] = {
    "hackernews": ("Hacker News", "https://hn.algolia.com/api/v1/search_by_date"),
    "lobsters": ("Lobsters", "https://lobste.rs/hottest.json"),
    "lemmy": ("Lemmy programming.dev", "https://programming.dev/api/v3/post/list"),
    "viblo": ("Viblo", "https://viblo.asia/api/posts"),
    "github_search": ("GitHub new repos", "https://api.github.com/search/repositories"),
    "hf_papers": ("Hugging Face Papers", "https://huggingface.co/api/daily_papers"),
    "github_trending": ("GitHub Trending", "https://github.com/trending"),
    "producthunt": ("Product Hunt", "https://www.producthunt.com/feed"),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KindDefaults(_Strict):
    cadence_hours: int = Field(default=24, gt=0)


class Defaults(_Strict):
    rss: KindDefaults = Field(default_factory=KindDefaults)
    # dev.to is polled every 3 h run (docs/PLAN.md §13 Ingestion).
    devto: KindDefaults = Field(default_factory=lambda: KindDefaults(cadence_hours=3))


class DevtoConfig(_Strict):
    tags: list[str] = Field(default_factory=list)
    base_weight: int = Field(default=50, ge=0, le=100)
    cadence_hours: int | None = Field(default=None, gt=0)


class RssEntry(_Strict):
    name: str = Field(min_length=1)
    url: str = Field(pattern=r"^https?://")
    category: str | None = None
    base_weight: int = Field(default=50, ge=0, le=100)
    cadence_hours: int | None = Field(default=None, gt=0)


class ApiEntry(_Strict):
    """A non-RSS source with its own fetcher module; name and URL come from API_SOURCES."""

    kind: ApiKind
    category: str | None = None
    base_weight: int = Field(default=50, ge=0, le=100)
    cadence_hours: int = Field(default=24, gt=0)


class SourcesFile(_Strict):
    defaults: Defaults = Field(default_factory=Defaults)
    devto: DevtoConfig = Field(default_factory=DevtoConfig)
    rss: list[RssEntry] = Field(default_factory=list)
    api: list[ApiEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> Self:
        for label, values in (
            ("rss url", [e.url for e in self.rss]),
            ("rss name", [e.name for e in self.rss]),
            ("devto tag", self.devto.tags),
            ("api kind", [e.kind for e in self.api]),
        ):
            dupes = sorted({v for v in values if values.count(v) > 1})
            if dupes:
                raise ValueError(f"duplicate {label}: {', '.join(dupes)}")
        return self

    def specs(self) -> list[SourceSpec]:
        """One SourceSpec per RSS feed, dev.to tag and API source; natural key (kind, url)."""
        specs = [
            SourceSpec(
                kind="rss",
                name=e.name,
                url=e.url,
                category=e.category,
                base_weight=e.base_weight,
                cadence_hours=e.cadence_hours or self.defaults.rss.cadence_hours,
            )
            for e in self.rss
        ]
        specs += [
            SourceSpec(
                kind="devto",
                name=f"dev.to #{tag}",
                url=DEVTO_TAG_URL.format(tag=tag),
                base_weight=self.devto.base_weight,
                cadence_hours=self.devto.cadence_hours or self.defaults.devto.cadence_hours,
            )
            for tag in self.devto.tags
        ]
        specs += [
            SourceSpec(
                kind=e.kind,
                name=API_SOURCES[e.kind][0],
                url=API_SOURCES[e.kind][1],
                category=e.category,
                base_weight=e.base_weight,
                cadence_hours=e.cadence_hours,
            )
            for e in self.api
        ]
        return specs


def load_sources(path: Path) -> SourcesFile:
    if not path.is_file():
        raise ConfigError(f"Sources file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Top level of {path} must be a mapping")
    try:
        return SourcesFile.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid sources in {path}:\n{_format_errors(exc)}") from exc
