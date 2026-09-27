from pathlib import Path

import pytest

from digest.config import ConfigError, load_config

MINIMAL = "profile: Backend developer.\n"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_minimal_config_uses_plan_defaults(tmp_path: Path) -> None:
    cfg = load_config(write(tmp_path, MINIMAL))

    assert cfg.profile == "Backend developer."
    assert cfg.timezone == "Asia/Ho_Chi_Minh"
    assert cfg.max_items == 10
    assert cfg.sources.hackernews.min_points == 50
    assert cfg.sources.lobsters.min_score == 10
    assert cfg.sources.reddit.min_score == 100
    assert cfg.sources.github_trending.min_stars_today == 50
    assert cfg.sources.rss == []
    assert cfg.filters.seen_retention_days == 60
    assert cfg.ai.enabled is False
    assert cfg.ai.output_language == "vi"


def test_full_config_is_parsed(tmp_path: Path) -> None:
    text = """
profile: |
  Go and Python.
max_items: 5
sources:
  hackernews: {enabled: false, min_points: 80}
  github_trending: {enabled: true, languages: [python, go], min_stars_today: 30}
  reddit: {enabled: true, subreddits: [golang], min_score: 50}
  rss:
    - {name: lwn, url: "https://lwn.net/headlines/rss", base_score: 60}
filters:
  keywords_include: [postgres]
  keywords_exclude: [crypto]
ai:
  providers:
    - name: groq
      base_url: https://api.groq.com/openai/v1
      model: llama-3.3-70b-versatile
      api_key_env: GROQ_API_KEY
delivery:
  pages: {enabled: true, base_url: "https://example.github.io/tech-digest/"}
"""
    cfg = load_config(write(tmp_path, text))

    assert cfg.max_items == 5
    assert cfg.sources.hackernews.enabled is False
    assert cfg.sources.github_trending.languages == ["python", "go"]
    assert cfg.sources.reddit.subreddits == ["golang"]
    assert cfg.sources.rss[0].name == "lwn"
    assert cfg.sources.rss[0].base_score == 60
    assert cfg.filters.keywords_exclude == ["crypto"]
    assert cfg.ai.providers[0].api_key_env == "GROQ_API_KEY"
    assert cfg.delivery.pages.base_url == "https://example.github.io/tech-digest/"


def test_missing_profile_names_the_field(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="profile"):
        load_config(write(tmp_path, "max_items: 10\n"))


def test_unknown_key_is_rejected_with_its_path(tmp_path: Path) -> None:
    text = MINIMAL + "sources:\n  hackernews: {min_pints: 10}\n"
    with pytest.raises(ConfigError, match=r"sources\.hackernews\.min_pints"):
        load_config(write(tmp_path, text))


def test_wrong_type_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="max_items"):
        load_config(write(tmp_path, MINIMAL + "max_items: many\n"))


def test_missing_file_gives_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_invalid_yaml_gives_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="YAML"):
        load_config(write(tmp_path, "profile: [unclosed\n"))


def test_unknown_timezone_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="timezone"):
        load_config(write(tmp_path, MINIMAL + "timezone: Mars/Olympus\n"))


def test_repo_config_yaml_is_valid() -> None:
    cfg = load_config(Path(__file__).parent.parent / "config.yaml")
    assert cfg.max_items == 10
