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

## Content store ingest (Part 2)

```bash
# Local Supabase only; the DB URL is read from the environment, never from .env.
DIGEST_DB_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres uv run python -m digest ingest
DIGEST_DB_URL=... uv run python -m digest health --dry-run  # list sources failing > 24 h
```

`ingest` syncs `sources.yaml`, fetches the sources that are due, enriches new items and
recomputes `pop_pct` (tunables under `ingest:` in `config.yaml`). Outside GitHub Actions a
non-local `DIGEST_DB_URL` is refused unless `--allow-remote` is passed. `ingest.yml` runs
it every 3 hours; `daily.yml` runs `health`, which sends one Telegram warning when sources
have been failing for more than 24 hours.

## AI scoring and summaries

Set `ai.enabled: true` in `config.yaml` and provide at least one provider key
(`GEMINI_API_KEY`, `GROQ_API_KEY`) in `.env` locally or as a GitHub Actions secret.
Providers are tried in order; with no working provider the digest is sent with the
heuristic ranking and an "AI unavailable" note. Prompts live in `src/digest/ai/prompts/`.

## Test and lint

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```
