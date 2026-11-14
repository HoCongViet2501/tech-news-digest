# Tech News Digest

Full plan: `docs/PLAN.md`. Read the section for the current phase before writing code.
Part 1 (phases 1–3) is done; current work is Part 2, the web app (phases W0–W5).

## Commands
- Install: `uv sync`
- Test: `uv run pytest`
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Offline end-to-end run: `DIGEST_OFFLINE=1 uv run python -m digest run --dry-run`
- Web (run in `web/`; Node from `web/.nvmrc`, pnpm from `packageManager`): `pnpm install`, `pnpm dev`, `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`
- Web e2e (first time: `pnpm exec playwright install chromium`): `pnpm e2e`
- Local DB (Docker, run in `web/`): `pnpm db:start`, `pnpm db:stop`, `pnpm db:reset`
- New migration (in `web/`): `pnpm exec supabase migration new <name>` (writes to `supabase/migrations/`)
- Regenerate DB types after a migration (local DB running): `pnpm db:types`, then commit `web/src/lib/database.types.ts`

## Conventions
- Python 3.12, full type hints, pydantic for all data crossing module boundaries.
- Fetchers never raise; on error, log and return `[]`.
- No real network calls in tests. Every new fetcher needs a fixture and a test.
- Never log API keys, tokens or Authorization headers.
- Prompts live in `src/digest/ai/prompts/`, never hardcoded.
- Anything tunable goes through `config.yaml`.
- Running cost must always be $0: only free tiers. When a free limit is near, degrade (retention, fewer calls) instead of proposing a paid plan.
- User-facing digest text (summaries, reasons) is written in `ai.output_language` (Vietnamese by default); code, comments, logs and commit messages are in English.

## Workflow
- Work on exactly one phase at a time, following the step order in PLAN.md.
- After each step: run tests + lint, then commit with a clear message.
- At the end of a phase: check every "acceptance" item and state which ones need manual verification by the owner.
- Do not add features outside the plan; write ideas to `docs/IDEAS.md` instead.
- Tasks listed under "Human-only setup" need account access: remind the owner, do not attempt them.
