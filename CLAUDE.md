# Tech News Digest

Full plan: `docs/PLAN.md`. Read the section for the current phase before writing code.

## Commands
- Install: `uv sync`
- Test: `uv run pytest`
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Offline end-to-end run: `DIGEST_OFFLINE=1 uv run python -m digest run --dry-run`

## Conventions
- Python 3.12, full type hints, pydantic for all data crossing module boundaries.
- Fetchers never raise; on error, log and return `[]`.
- No real network calls in tests. Every new fetcher needs a fixture and a test.
- Never log API keys, tokens or Authorization headers.
- Prompts live in `src/digest/ai/prompts/`, never hardcoded.
- Anything tunable goes through `config.yaml`.
- User-facing digest text (summaries, reasons) is written in `ai.output_language` (Vietnamese by default); code, comments, logs and commit messages are in English.

## Workflow
- Work on exactly one phase at a time, following the step order in PLAN.md.
- After each step: run tests + lint, then commit with a clear message.
- At the end of a phase: check every "acceptance" item and state which ones need manual verification by the owner.
- Do not add features outside the plan; write ideas to `docs/IDEAS.md` instead.
- Tasks listed under "Human-only setup" need account access: remind the owner, do not attempt them.
