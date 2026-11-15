"""Load and validate config.yaml into pydantic models.

Secrets are never stored here: providers only name the env var holding their key.
"""

from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


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


class TldrConfig(_Strict):
    """TL;DR for the Popular pool (docs/PLAN.md §13 Ingestion step 9, §15 LLM budget).

    The Gemini free quota is shared with the daily Telegram digest and the tagger, so
    the defaults stay small: 2 calls x 8 runs is at most 16 calls (160 items) a day."""

    # Top N of popular_feed('popular') (one item per cluster) that should have a TL;DR.
    pool_size: int = Field(default=100, ge=0)
    calls_per_run: int = Field(default=2, ge=0)  # LLM batches per run; 0 turns the stage off
    batch_size: int = Field(default=10, gt=0)
    # A pool item still without a TL;DR is retried each run until it is this old
    # (first_seen_at), then marked summarized without one.
    max_age_hours: int = Field(default=72, gt=0)
    # Article text sent per item; 10 x 1500 chars keeps a batch under ~6k input tokens,
    # inside the smallest free-tier per-minute token limit in the provider chain.
    input_chars: int = Field(default=1500, gt=0)
    # Less text than this (article or description) and the item is skipped: there is
    # not enough to summarize honestly.
    min_chars: int = Field(default=120, ge=0)
    max_chars: int = Field(default=600, gt=0)  # longer answers are cut at a sentence end


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
    tldr: TldrConfig = Field(default_factory=TldrConfig)


class RankingConfig(_Strict):
    """Per-user ranking knobs (docs/PLAN.md §15). Synced into the ranking_config table at
    the start of every ingest run; the ranking itself lives only in SQL (my_feed,
    feed_for_user, popular_feed). Bounds mirror the table's CHECK constraints."""

    # score = w_interest·interest + w_popularity·popularity + w_quality·quality
    #         + w_freshness·freshness
    w_interest: float = Field(default=0.40, ge=0, le=1)
    w_popularity: float = Field(default=0.25, ge=0, le=1)
    w_quality: float = Field(default=0.20, ge=0, le=1)
    w_freshness: float = Field(default=0.15, ge=0, le=1)
    # interest = t·tag_match + (1 - t)·cos(user_vec, item): t is interest_tag_weight, or
    # cold_start_tag_weight while user_vec is still the followed-tags centroid.
    interest_tag_weight: float = Field(default=0.5, ge=0, le=1)
    cold_start_tag_weight: float = Field(default=0.7, ge=0, le=1)
    affinity_weight: float = Field(default=0.5, ge=0, le=1)  # tag_affinity's share of tag_match
    multi_source_bonus: float = Field(default=0.2, ge=0, le=1)  # cluster seen on >= 2 sites
    freshness_hours: float = Field(default=36, gt=0)  # freshness = exp(-age_hours / this)
    default_quality: float = Field(default=0.5, ge=0, le=1)  # items the LLM has not scored
    pool_days: int = Field(default=7, ge=1, le=30)  # candidate pool (and Popular) window
    centroid_days: int = Field(default=7, ge=1, le=90)  # cold-start centroid window
    candidates: int = Field(default=200, ge=10, le=500)  # top-scored items kept for MMR
    mmr_lambda: float = Field(default=0.7, ge=0, le=1)  # 1 = pure relevance, 0 = pure diversity
    page_size: int = Field(default=24, ge=1, le=60)  # matches the web's PAGE_SIZE
    max_per_source: int = Field(default=3, ge=1)  # per page_size window
    explore_ratio: float = Field(default=0.125, ge=0, le=0.5)  # share of Popular explore slots
    snapshot_minutes: int = Field(default=30, gt=0)  # a first page reuses a snapshot this young

    @model_validator(mode="after")
    def _popular_weights(self) -> Self:
        if self.w_popularity + self.w_quality + self.w_freshness <= 0:
            raise ValueError("w_popularity + w_quality + w_freshness must be > 0")
        return self


class Config(_Strict):
    profile: str
    timezone: str = "Asia/Ho_Chi_Minh"
    max_items: int = Field(default=10, gt=0)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    ranking: RankingConfig = Field(default_factory=RankingConfig)

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
