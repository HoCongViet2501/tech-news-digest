# tech-news-digest

A daily, personalized tech news digest: collected from Hacker News, GitHub Trending,
Lobsters, Reddit and RSS, filtered to at most 10 items, sent to Telegram and archived
on GitHub Pages. See [docs/PLAN.md](docs/PLAN.md) for the full plan.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # fill in secrets for real runs
```

## Run

```bash
uv run python -m digest run --dry-run          # print the digest, send nothing
uv run python -m digest run --only hn,lobsters # subset of sources
uv run python -m digest run --date 2026-09-01  # re-run a specific day
DIGEST_OFFLINE=1 uv run python -m digest run --dry-run  # use test fixtures, no network
```

Behavior is configured in `config.yaml`.

## Test and lint

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```
