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

Lists the owner's direct dependencies from manifests in their GitHub repos and adds a
"Radar" section at the top of the digest, only on days with something new:
vulnerabilities in the declared version (OSV.dev), the first release of a new major
line, and releases whose notes mention breaking changes. Each entry is reported once.

```yaml
radar:
  enabled: true
  manifests:
    - repo: <user>/web-app
      paths: [package.json]
    - repo: <user>/api-service
      paths: [go.mod, requirements.txt, pyproject.toml]
  include_dev_dependencies: false
  ignore: [typescript, eslint]
```

- Version ranges are reduced to their lower bound (`^4.17.15` -> 4.17.15); unpinned
  dependencies are only watched for breaking releases.
- The first run records each repo's current releases as a baseline and reports none of
  them; vulnerabilities are reported from day one.
- GitHub calls use `GH_READ_TOKEN` (needed for private repos: fine-grained PAT,
  Contents: read) or else the workflow's `GITHUB_TOKEN`.
- State: `data/radar_state.json` (what was reported, known release tags) and
  `data/radar_map.json` (package -> GitHub repo cache).

## Test and lint

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```
