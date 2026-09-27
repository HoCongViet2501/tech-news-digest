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
    providers: list[AIProviderConfig] = Field(default_factory=list)


class TelegramConfig(_Strict):
    enabled: bool = True


class PagesConfig(_Strict):
    enabled: bool = True
    base_url: str = ""


class DeliveryConfig(_Strict):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    pages: PagesConfig = Field(default_factory=PagesConfig)


class Config(_Strict):
    profile: str
    timezone: str = "Asia/Ho_Chi_Minh"
    max_items: int = Field(default=10, gt=0)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)

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
