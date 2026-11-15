"""Load and validate config.yaml into pydantic models.

Secrets are never stored here: providers only name the env var holding their key.
"""

from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ConfigError(Exception):
    """Raised when config.yaml is missing, unparsable or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HackerNewsConfig(_Strict):
    enabled: bool = True
    min_points: int = 50
    # Lower bound sent to the API; min_points itself is applied in filter.py.
    fetch_min_points: int = 10


class GitHubTrendingConfig(_Strict):
    enabled: bool = True
    languages: list[str] = Field(default_factory=lambda: ["python", "go"])
    min_stars_today: int = 50


class LobstersConfig(_Strict):
    enabled: bool = True
    min_score: int = 10


class RedditConfig(_Strict):
    enabled: bool = True
    subreddits: list[str] = Field(default_factory=lambda: ["programming"])
    min_score: int = 100


class RssFeedConfig(_Strict):
    name: str
    url: str
    base_score: int = 60


class SourcesConfig(_Strict):
    hackernews: HackerNewsConfig = Field(default_factory=HackerNewsConfig)
    github_trending: GitHubTrendingConfig = Field(default_factory=GitHubTrendingConfig)
    lobsters: LobstersConfig = Field(default_factory=LobstersConfig)
    reddit: RedditConfig = Field(default_factory=RedditConfig)
    rss: list[RssFeedConfig] = Field(default_factory=list)


class FiltersConfig(_Strict):
    keywords_include: list[str] = Field(default_factory=list)
    keywords_exclude: list[str] = Field(default_factory=list)
    seen_retention_days: int = 60


class AIProviderConfig(_Strict):
    name: str
    base_url: str
    model: str
    api_key_env: str


class AIConfig(_Strict):
    enabled: bool = False
    output_language: str = "vi"
    candidates: int = Field(default=60, gt=0)  # heuristic top-N sent to the AI scorer
    summary_batch_size: int = Field(default=5, gt=0)
    article_max_chars: int = Field(default=4000, gt=0)
    providers: list[AIProviderConfig] = Field(default_factory=list)


class TelegramConfig(_Strict):
    enabled: bool = True


class PagesConfig(_Strict):
    enabled: bool = True
    base_url: str = ""


class DeliveryConfig(_Strict):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    pages: PagesConfig = Field(default_factory=PagesConfig)


class GitHubSearchIngestConfig(_Strict):
    """Query window and spam thresholds for the github_search fetcher."""

    days: int = Field(default=7, gt=0)
    max_fork_ratio: float = Field(default=0.5, gt=0)
    min_fork_ratio: float = Field(default=0.005, ge=0)
    zero_fork_min_stars: int = Field(default=200, ge=0)


class EmbedConfig(_Strict):
    """Local embeddings (fastembed). The model is cached in cache_dir, which ingest.yml
    keeps with actions/cache so runners do not download it every run."""

    model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    cache_dir: str = ".cache/fastembed"
    batch_size: int = Field(default=64, gt=0)
    threads: int | None = Field(default=None, gt=0)  # None: onnxruntime picks
    per_run: int = Field(default=2000, ge=0)  # items embedded per run
    max_chars: int = Field(default=1000, gt=0)  # title + description, cut (model keeps 128 tokens)


class TaggingConfig(_Strict):
    """Four-step tagger (docs/PLAN.md §15 Tagging). Confidences are fixed per step; the
    embedding step maps cosine similarity into [embed_confidence_min, embed_confidence_max]."""

    per_run: int = Field(default=2000, ge=0)  # items taken off the tag queue per run
    native_confidence: float = Field(default=0.95, ge=0, le=1)
    rule_confidence: float = Field(default=0.9, ge=0, le=1)  # keyword in the title
    # Keyword only in the description: often a passing mention ("Available for Windows,
    # macOS, Linux"), so below `confident` and left for the LLM to confirm or replace.
    rule_description_confidence: float = Field(default=0.7, ge=0, le=1)
    # A tag at or above this is "confident": later steps (embedding, LLM) are skipped.
    confident: float = Field(default=0.8, ge=0, le=1)
    embed_threshold: float = Field(default=0.55, ge=-1, le=1)  # cosine vs tag description
    # Per-slug overrides: "frontend" matched backend posts, "crypto-web3" cryptography ones.
    embed_thresholds: dict[str, float] = Field(
        default_factory=lambda: {"frontend": 0.7, "crypto-web3": 0.6}
    )
    embed_confidence_min: float = Field(default=0.6, ge=0, le=1)
    embed_confidence_max: float = Field(default=0.75, ge=0, le=1)
    embed_max_tags: int = Field(default=2, ge=0)
    # LLM fallback: only items whose best tag-description cosine reaches this (a coarse
    # "is it tech at all" filter), with no confident tag, first seen within llm_max_age_hours.
    llm_min_similarity: float = Field(default=0.25, ge=-1, le=1)
    llm_batch_size: int = Field(default=20, gt=0)
    llm_confidence: float = Field(default=0.85, ge=0, le=1)
    llm_max_age_hours: int = Field(default=24, gt=0)
    llm_description_chars: int = Field(default=300, gt=0)


class ClusterConfig(_Strict):
    """Near-duplicate clustering (docs/PLAN.md §15 Dedupe)."""

    window_hours: int = Field(default=72, gt=0)
    title_threshold: float = Field(default=0.92, ge=-1, le=1)
    # Only pairs whose stored (title + description) vectors reach this get their titles
    # embedded and compared; keeps the per-run cost to a fraction of the window.
    prefilter: float = Field(default=0.5, ge=-1, le=1)


class IngestConfig(_Strict):
    """`python -m digest ingest` (Part 2 content store, docs/PLAN.md §13 Ingestion)."""

    enrich_per_run: int = Field(default=150, ge=0)
    concurrency: int = Field(default=8, gt=0)  # sources (and pages) fetched at once
    per_host: int = Field(default=2, gt=0)  # at most this many in flight per host
    enrich_timeout_s: float = Field(default=10, gt=0)
    due_slack_minutes: int = Field(default=15, ge=0)  # absorbs cron start jitter
    pop_window_days: int = Field(default=7, gt=0)
    # RSS entries older than this are skipped, so a first fetch never imports an archive.
    rss_max_age_days: int = Field(default=30, gt=0)
    github_search: GitHubSearchIngestConfig = Field(default_factory=GitHubSearchIngestConfig)
    llm_calls_per_run: int = Field(default=20, ge=0)  # LLM tag + quality batches per run
    embed: EmbedConfig = Field(default_factory=EmbedConfig)
    tagging: TaggingConfig = Field(default_factory=TaggingConfig)
    cluster: ClusterConfig = Field(default_factory=ClusterConfig)


class Config(_Strict):
    profile: str
    timezone: str = "Asia/Ho_Chi_Minh"
    max_items: int = Field(default=10, gt=0)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "<root>"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


def load_config(path: Path) -> Config:
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Top level of {path} must be a mapping")
    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid config in {path}:\n{_format_errors(exc)}") from exc
