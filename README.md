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

A real run sends to Telegram (needs `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the
environment or `.env`), then writes `data/seen.json`, `data/digests/YYYY-MM-DD.json` and
the static site in `site/`. With `DIGEST_OFFLINE=1` and no `--dry-run`, Telegram is replaced
by a stub that prints the messages, and state is still written; pass `--data-dir` and
`--site-dir` pointing to a scratch directory to keep fixture data out of `data/`.

Behavior is configured in `config.yaml`.

## AI scoring and summaries

Set `ai.enabled: true` in `config.yaml` and provide at least one provider key
(`GEMINI_API_KEY`, `GROQ_API_KEY`) in `.env` locally or as a GitHub Actions secret.
Providers are tried in order; with no working provider the digest is sent with the
heuristic ranking and an "AI unavailable" note. Prompts live in `src/digest/ai/prompts/`.

## Dependency radar

`radar.manifests` in `config.yaml` lists the repos and manifest files to watch
(`package.json`, `requirements*.txt`, `pyproject.toml`, `go.mod`). Each day the radar
reports, at the top of the digest and only when there is something new:

1. vulnerabilities affecting the declared version (OSV, no key needed),
2. new major versions,
3. releases whose notes mention breaking changes.

Each vulnerability and release is reported once (`data/radar_state.json`); package to
GitHub repo lookups are cached in `data/radar_map.json`. The GitHub API is called with
`GH_READ_TOKEN` if set (needed only for private repos, fine-grained PAT with Contents:
read), otherwise `GITHUB_TOKEN`, which the daily workflow passes automatically.

## Test and lint

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```
