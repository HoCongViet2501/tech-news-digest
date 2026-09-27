# Tech News Digest — Implementation Plan

A daily, personalized tech news digest. Every morning at 07:00 (Asia/Ho_Chi_Minh), a GitHub Actions job collects stories from 5–6 sources, filters them down to at most 10 items that match the owner's profile, sends them to Telegram, and archives them on GitHub Pages with an RSS feed. Running cost: zero.

Work is split into 4 phases. Each phase ends with a working, shippable version; the project can stop after any phase.

---

## 1. Goals, scope and key decisions

**Goals**

- The digest can be read in ~3 minutes: max 10 items per day.
- Never resend an item that was sent on a previous day.
- No server, no paid component.
- Every phase ends in a runnable state.

**Out of scope:** a custom web/app with login, multi-user support, realtime updates.

| Area | Choice | Why |
| --- | --- | --- |
| Language | Python 3.12 | feedparser, trafilatura, sentence-transformers available |
| Packaging | uv + `pyproject.toml` | Fast, lock file, good GitHub Actions support |
| HTTP | httpx (async) | Timeouts, retries, easy to mock |
| HTML / RSS parsing | beautifulsoup4, feedparser | Scrape GitHub Trending, read RSS |
| Article extraction | trafilatura | Main body text for AI summaries |
| LLM client | `openai` SDK with configurable `base_url` | Gemini, Groq, OpenRouter all expose OpenAI-compatible endpoints |
| Config | YAML + pydantic | Validate early, clear errors |
| State | JSON files in the repo, committed by Actions | No DB; history via git |
| Schedule | GitHub Actions cron `0 0 * * *` (UTC) | = 07:00 Vietnam time |
| Delivery | Telegram Bot API | Push notifications, inline buttons for feedback |
| Archive | GitHub Pages (static HTML + `feed.xml`) | Free, readable in any RSS app |
| Templates | Jinja2 | Shared by Telegram, HTML and RSS output |
| Tests | pytest + respx | Mock HTTP; no real network in unit tests |

---

## 2. Repository layout and modules

Each pipeline step is its own module and communicates only through the models in `models.py`, so every step can be tested and replaced independently.

```
fetchers/ → normalize.py → filter.py → ai/ (phase 2) → render.py → deliver/
 sources      Item model,     dedupe,      score by        Jinja2:        Telegram,
 HN, GitHub,  canonical URL   rules,       profile,        Telegram,      Pages build,
 Lobsters,                    seen.json    summarize top   HTML,          save state
 Reddit, RSS                               10              feed.xml
```

`uv run python -m digest run` executes the steps in order, then saves state.
`--dry-run` prints the digest to stdout, sends nothing and writes no state.
`--only hn,lobsters` runs a subset of sources for debugging.
`--date YYYY-MM-DD` re-runs a specific day.

```
tech-digest/
├── pyproject.toml
├── config.yaml              # profile, sources, thresholds, AI providers
├── CLAUDE.md                # conventions for the coding agent
├── docs/PLAN.md             # this file
├── src/digest/
│   ├── __main__.py          # CLI: run, --dry-run, --only, --date
│   ├── config.py            # pydantic models, loads YAML + env vars
│   ├── models.py            # Item, ScoredItem, Digest
│   ├── http.py              # shared httpx client: timeout, retry, User-Agent
│   ├── fetchers/
│   │   ├── base.py          # Fetcher protocol
│   │   ├── hackernews.py
│   │   ├── github_trending.py
│   │   ├── lobsters.py
│   │   ├── reddit.py
│   │   └── rss.py
│   ├── normalize.py         # canonical URLs, merge duplicates
│   ├── filter.py            # thresholds, include/exclude keywords, seen
│   ├── state.py             # read/write data/*.json
│   ├── ai/                  # phase 2
│   │   ├── llm.py           # provider chain + fallback
│   │   ├── scorer.py
│   │   ├── summarizer.py
│   │   └── prompts/
│   ├── radar/               # phase 3
│   ├── render.py            # Jinja2 → Telegram HTML, web pages, feed.xml
│   ├── deliver/
│   │   ├── telegram.py
│   │   └── pages.py
│   └── templates/
├── data/
│   ├── seen.json            # sent item ids, kept 60 days
│   ├── digests/             # YYYY-MM-DD.json, one per day
│   └── feedback.jsonl       # phase 4
├── site/                    # Pages build output, not committed
├── scripts/
│   └── refresh_fixtures.py  # download real sample responses into tests/fixtures
├── tests/
│   └── fixtures/            # real JSON/HTML samples per source
└── .github/workflows/
    ├── daily.yml
    └── ci.yml
```

---

## 3. Data model, config and secrets

All sources are converted into a single `Item` model. From the filter step onward, code never needs to know where an item came from. All behavior is driven by `config.yaml`; keys live in GitHub Secrets.

### Models (`models.py`)

```python
class Item(BaseModel):
    id: str                     # sha1 of the canonical URL
    source: str                 # "hn" | "github" | "lobsters" | "reddit" | "rss:<name>"
    title: str
    url: str                    # canonical URL
    discussion_url: str | None  # HN / Reddit / Lobsters discussion link
    score: int                  # source-native score (points, stars today, ...)
    comments: int = 0
    published_at: datetime
    tags: list[str] = []
    also_on: list[str] = []     # other sources that had the same URL

class ScoredItem(Item):
    relevance: float            # 0-10; phase 1 = heuristic, phase 2 = AI
    reason: str | None = None
    summary: str | None = None
    why_it_matters: str | None = None

class Digest(BaseModel):
    date: date
    items: list[ScoredItem]
    stats: dict[str, int | str] # fetched, after_dedupe, after_filter, sent, provider, warnings
```

- `data/seen.json`: map `{item_id: "YYYY-MM-DD"}`. Each run prunes entries older than `seen_retention_days`.
- `data/digests/YYYY-MM-DD.json`: the sent `Digest`, used to rebuild the website and RSS.

### `config.yaml` example

```yaml
timezone: Asia/Ho_Chi_Minh
max_items: 10

profile: |
  Backend developer, Go and Python, Postgres, Kubernetes.
  Interested in: LLM tooling, system design, database performance, developer productivity.
  Not interested in: crypto, hiring drama, frontend framework wars.

sources:
  hackernews: {enabled: true, min_points: 50}
  github_trending: {enabled: true, languages: [python, go], min_stars_today: 50}
  lobsters: {enabled: true, min_score: 10}
  reddit: {enabled: true, subreddits: [programming, golang, Python], min_score: 100}
  rss: []   # entries like {name: ..., url: ..., base_score: 60}

filters:
  keywords_include: [postgres, kubernetes, llm, golang]   # always kept, even below threshold
  keywords_exclude: [crypto, bitcoin, nft]
  seen_retention_days: 60

ai:
  enabled: false            # turned on in phase 2
  output_language: vi       # summaries and reasons are written in Vietnamese
  providers:                # tried in order; on failure move to the next
    - name: gemini
      base_url: https://generativelanguage.googleapis.com/v1beta/openai/
      model: gemini-2.5-flash-lite
      api_key_env: GEMINI_API_KEY
    - name: groq
      base_url: https://api.groq.com/openai/v1
      model: llama-3.3-70b-versatile
      api_key_env: GROQ_API_KEY

delivery:
  telegram: {enabled: true}
  pages: {enabled: true, base_url: https://<username>.github.io/tech-digest/}
```

Provider model names change often; verify the current names before enabling phase 2.

### Secrets

| Variable | Needed from | Where to get it |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Phase 1 | @BotFather, `/newbot` |
| `TELEGRAM_CHAT_ID` | Phase 1 | Message the bot once, then call `getUpdates`; or use a channel id |
| `GEMINI_API_KEY` | Phase 2 | Google AI Studio |
| `GROQ_API_KEY` | Phase 2 | console.groq.com |
| `OPENROUTER_API_KEY` | Phase 2, optional | openrouter.ai |
| `GH_READ_TOKEN` | Phase 3, only for private repos | Fine-grained PAT, Contents: read |

Locally, put these in `.env` (gitignored) and load with python-dotenv. On GitHub, add them under Settings → Secrets and variables → Actions.

---

## 4. Phase 1 — MVP without AI

At the end of phase 1 the owner receives 10 items on Telegram every morning, ranked by a heuristic, plus an archive page and RSS feed on GitHub Pages. This is the most important phase: later phases only plug into this pipeline.

### Sources

| Source | Endpoint | Default filter | Notes |
| --- | --- | --- | --- |
| Hacker News | Algolia: `https://hn.algolia.com/api/v1/search_by_date?tags=story&numericFilters=created_at_i>{now-24h},points>{min}` | ≥ 50 points | No key. Ask HN posts without a URL use the discussion link as `url` |
| GitHub Trending | Scrape `https://github.com/trending/{lang}?since=daily` | ≥ 50 stars today | No official API; HTML may change. If parsing yields 0 repos, fall back to the Search API: repos created in the last 7 days, sorted by stars |
| Lobsters | `https://lobste.rs/hottest.json` | ≥ 10 score | Low noise, high quality |
| Reddit | `https://www.reddit.com/r/{sub}/top.json?t=day&limit=25` | ≥ 100 upvotes | Custom User-Agent required. On 403/429 skip and log |
| RSS | feedparser on URLs from config | Entries from the last 36 hours | No native score; use `base_score` from config |

### Steps

1. **Scaffold.**
    - `uv init`; dependencies: httpx, pydantic, pyyaml, feedparser, beautifulsoup4, jinja2, python-dotenv. Dev: pytest, respx, ruff.
    - `config.py` loads `config.yaml` into pydantic models; missing required keys produce a clear error.
    - CLI with argparse: `run`, `--dry-run`, `--only`, `--date`.
    - `ci.yml` runs `ruff check` and `pytest` on every push.
2. **Fixtures first.** Write `scripts/refresh_fixtures.py` that downloads one real response per source into `tests/fixtures/`. Commit the fixtures.
3. **Fetchers.**
    - Each fetcher implements `async def fetch(cfg) -> list[Item]` and never raises: on error, log and return `[]`.
    - Run concurrently with `asyncio.gather`; 15 s timeout per source; 2 retries on 5xx.
    - Every fetcher has a parse test against its fixture.
    - When env `DIGEST_OFFLINE=1`, fetchers read fixtures instead of the network, and Telegram delivery is replaced by a stub that prints to stdout and reports success, so a non-dry-run offline run still writes state (used to verify "running twice does not repeat items").
4. **Normalize.**
    - Canonical URL: lowercase host, strip `www.`, strip `utm_*`, `ref`, `fbclid`, fragment and trailing `/`.
    - `id` = sha1 of the canonical URL.
    - Merge duplicates: keep the highest-scoring copy, record the other sources in `also_on`.
5. **Filter and heuristic ranking.**
    - Drop items present in `seen.json` and items matching `keywords_exclude` (whole word, case-insensitive, on title).
    - Apply per-source thresholds; items matching `keywords_include` are kept even below threshold.
    - `relevance` = percentile of the item's score within its source × 10, +3 if it matches an include keyword, +1 per entry in `also_on`. Take the top `max_items`.
6. **Render.**
    - Telegram uses `parse_mode=HTML` (only b, i, a, code tags; escape `& < >`). Per item: number, linked title, source + score, discussion link.
    - Split into multiple messages above 4,096 characters, never splitting an item.
    - Web: `index.html` shows the latest digest and an archive list; one page per day `YYYY-MM-DD.html`; supports dark mode and mobile.
    - `feed.xml` in RSS 2.0, one `<item>` per story, last 30 days.
7. **Deliver and save state.**
    - `sendMessage` with `disable_web_page_preview=true` so 10 links don't produce 10 preview cards.
    - Write `seen.json` and `digests/YYYY-MM-DD.json` only after Telegram delivery succeeds.
    - If every source fails, send a warning message instead of an empty digest. If Telegram delivery fails, exit non-zero so GitHub emails the owner.
8. **Workflow `daily.yml`.**
    - Triggers: `schedule` with cron `0 0 * * *` and `workflow_dispatch` for manual runs.
    - `permissions: contents: write, pages: write, id-token: write`; a `concurrency` group to prevent overlapping runs.
    - Steps: checkout → setup uv → `uv sync` → `python -m digest run` → commit `data/` with message `digest: YYYY-MM-DD` → `actions/upload-pages-artifact` for `site/` → `actions/deploy-pages`.
    - GitHub disables scheduled workflows in public repos after 60 days without activity; the daily `data/` commit keeps the repo active.

### Phase 1 acceptance

- [ ] `DIGEST_OFFLINE=1 uv run python -m digest run --dry-run` prints ≤ 10 items from ≥ 3 sources.
- [ ] Running twice in a row: the second run does not repeat items from the first.
- [ ] One source failing (mocked error) still produces a digest from the remaining sources.
- [ ] Manual workflow run: Telegram message received, Pages shows today's digest, `feed.xml` parses with feedparser.
- [ ] `pytest` passes with no real network access.

---

## 5. Phase 2 — AI scoring and summaries

AI replaces the heuristic ranking and adds a short summary plus a one-line "why it matters to you". Total usage is about 3–5 requests per day, well within the free tiers of Gemini or Groq.

### Steps

1. **`ai/llm.py` — provider chain with fallback.**
    - Each provider is an `openai.OpenAI(base_url=..., api_key=...)` client built from config; skip providers whose key is missing.
    - `complete(messages, json_mode=True)` tries providers in order: on 429, 5xx, a 60 s timeout, or invalid JSON, move to the next.
    - Tolerant JSON parsing: strip ```` ```json ```` fences, cut from the first `[` or `{`, validate with pydantic.
    - Log which provider answered and token counts; store them in the digest `stats`.
2. **Widen the pre-filter.** When `ai.enabled`, `filter.py` returns the heuristic top 60 instead of top 10, so the AI has enough candidates.
3. **`ai/scorer.py` — one request for the whole list.**
    - Input: profile + array of `{id, title, source, score, domain}`.
    - Output: `[{id, score: 0-10, reason: "<=15 words"}]`. Unknown ids are ignored; missing items keep their heuristic score.
    - Sort by AI score, break ties with the heuristic; take the top `max_items`.
4. **`ai/summarizer.py` — summarize the top 10.**
    - Extract the article with trafilatura (10 s timeout, truncate to 4,000 chars).
    - If extraction fails (paywall, JS-rendered page), use the top 3 HN comments via `https://hn.algolia.com/api/v1/items/{id}`, or the repo description for GitHub items.
    - 5 items per request. Output: `{id, summary: 2-3 sentences, why_it_matters: 1 sentence tied to the profile}`.
5. **Prompts live in `ai/prompts/*.md`**, never hardcoded. Shared rules: respond with JSON only, write in `output_language`, keep technical terms in English, never invent details not present in the input.
6. **Update render:** add summary, "why it matters" and relevance score to Telegram, web pages and RSS.
7. **When every provider fails:** send the phase-1 style digest with a note "AI unavailable today". Optional extra: a `embed` dependency group using sentence-transformers with `paraphrase-multilingual-MiniLM-L12-v2`, scoring by cosine similarity between the profile and titles, fully local and keyless.

### Example scoring prompt (`ai/prompts/score.md`)

```markdown
You are a tech news editor for a developer with this profile:
<profile>{profile}</profile>

Score each item from 0 to 10 by how useful it is to this specific person:
- 9-10: directly affects the stack they use (major release, vulnerability, breaking change)
- 6-8: on a topic they care about, with real technical depth
- 3-5: general tech news, fine to read
- 0-2: on their not-interested list, or just drama / marketing

Write each "reason" in {output_language}, at most 15 words.
Return only a JSON array, nothing else:
[{"id": "...", "score": 7, "reason": "..."}]

Items:
{items_json}
```

### Phase 2 acceptance

- [ ] Unit test with a fake LLM: when the first provider returns 429, the second one is called.
- [ ] JSON wrapped in fences or surrounded by extra text still parses; broken JSON triggers fallback, not a crash.
- [ ] With no keys configured, the digest is still sent in phase-1 style.
- [ ] Real run for 3 consecutive days; owner judges at least 7/10 items worth reading. If not, tune profile and prompt before phase 3.

---

## 6. Phase 3 — Dependency radar

The radar tracks exactly the libraries used in the owner's projects and reports three kinds of news: vulnerabilities affecting the installed version, new major versions, and releases with breaking changes. A "Radar" section appears at the top of the digest only when there is something to report, ordered: vulnerabilities → majors → other releases.

```yaml
radar:
  enabled: true
  manifests:
    - repo: <user>/project-a
      paths: [package.json]
    - repo: <user>/api-service
      paths: [go.mod, requirements.txt]
  include_dev_dependencies: false
  ignore: [typescript, eslint]
```

### Steps

1. **Read manifests** via the GitHub Contents API (`GET /repos/{owner}/{repo}/contents/{path}`), using `GH_READ_TOKEN` for private repos. Parse `package.json`, `requirements.txt`, `pyproject.toml` and `go.mod` into `{ecosystem, name, version}`.
2. **Map packages to GitHub repos.**
    - npm: `repository.url` from `https://registry.npmjs.org/{name}`.
    - PyPI: `project_urls` from `https://pypi.org/pypi/{name}/json`.
    - Go: module paths like `github.com/x/y` are used directly.
    - Cache results in `data/radar_map.json`; if no repo is found, track vulnerabilities only.
3. **New releases.** `GET /repos/{owner}/{repo}/releases?per_page=5`, compared against `data/radar_state.json` so each release is reported once. Compare semver with the installed version to flag majors. Flag release notes containing "breaking".
4. **Vulnerabilities.** `POST https://api.osv.dev/v1/querybatch` with `{package: {name, ecosystem}, version}` for all dependencies in one request. Free, no key, supports npm, PyPI and Go. Report each vulnerability id once.
5. **AI (if enabled)** condenses release notes into 1–2 sentences in `output_language` and answers one question: does upgrading require action?
6. **Rate limits.** Unauthenticated GitHub API allows only 60 requests/hour. In Actions use the built-in `GITHUB_TOKEN` (or `GH_READ_TOKEN`) and only query releases for direct dependencies.

### Phase 3 acceptance

- [ ] A test manifest declaring `lodash@4.17.15` makes the radar report a vulnerability from OSV.
- [ ] Two consecutive days do not report the same vulnerability or release twice.
- [ ] On days with nothing new, the Radar section is omitted.
- [ ] A broken manifest (wrong path, no permission) does not break the digest.

---

## 7. Phase 4 — Feedback loop and weekly digest

The owner taps like/dislike under the Telegram digest, and later scoring uses that history as examples. The only technical challenge is that there is no server to receive button events, so this phase uses polling.

### Steps

1. **Add buttons to the digest.** Send `reply_markup` as an inline keyboard, one row per item with like/dislike buttons for item n. `callback_data` format: `fb:{first 8 chars of item_id}:{up|down}`, within Telegram's 64-byte limit.
2. **Collect feedback with a `feedback.yml` workflow.**
    - Cron every 3 hours (`0 */3 * * *`) calls `getUpdates` with an `offset` stored in `data/telegram_offset.json`. Telegram only keeps unfetched updates for 24 hours, so do not space runs further apart.
    - Append each tap to `data/feedback.jsonl`: `{ts, item_id, vote, title, source}`. If the same item is voted again, the latest vote wins.
    - `getUpdates` does not work while a webhook is set; never set a webhook on this bot.
    - Trade-off: buttons show a loading spinner for a while because nothing answers immediately. Instant feedback would require a webhook on Cloudflare Workers, out of scope here.
3. **Use feedback when scoring.**
    - Include up to 15 liked and 15 disliked titles from the last 30 days as examples in the scoring prompt.
    - Heuristic: multiply each source's scores by a weight derived from its like ratio, clamped to 0.5–1.5.
4. **Weekly digest.** Cron `0 1 * * 0` (08:00 Sunday, Vietnam time) sends the week's top 5 by score and feedback, plus stats: items sent, like ratio, most useful source.
5. **Profile suggestions.** In the weekly digest, the AI reads the feedback and proposes at most 3 profile changes. Only suggest; never edit `config.yaml` automatically.

### Phase 4 acceptance

- [ ] Tapping a button on Telegram results in a matching line in `feedback.jsonl` within 3 hours.
- [ ] Running `feedback.yml` twice in a row does not duplicate entries (offset works).
- [ ] The scoring prompt includes feedback examples when data exists, and still works when the file is empty.
- [ ] The weekly digest is sent on Sunday and does not collide with the daily digest.

---

## 8. Testing and risks

All tests must run without network access and without keys, so the coding agent can verify its own work after every step.

### Test strategy

- **Unit tests per module**; HTTP mocked with respx; the LLM replaced by a fake client that returns fixed JSON or raises errors per scenario.
- **Real fixtures** per source in `tests/fixtures/`, refreshed with `scripts/refresh_fixtures.py` when a source changes format.
- **Offline mode**: `DIGEST_OFFLINE=1` makes fetchers read fixtures and stubs Telegram delivery, enabling an end-to-end `--dry-run` as well as offline runs that write state.
- **Snapshot tests** for rendered output (Telegram message, HTML, `feed.xml`), including titles containing `& < >` and emoji.
- **Time**: tests pass a fixed date via `--date`; never depend on the machine clock.

### Risks

| Risk | Symptom | Mitigation |
| --- | --- | --- |
| GitHub Trending HTML changes | Fetcher returns 0 repos | Fall back to Search API; record a warning in `stats` |
| Reddit blocks requests | 403 or 429 | Skip the source, never fail the job |
| AI free tier tightened or model renamed | 429, 404 model not found | Provider chain; worst case send phase-1 style digest |
| GitHub cron delay | Digest arrives at 07:20 instead of 07:00 | Accept; if exact timing matters, move to Cloudflare Workers |
| Telegram rejects HTML | 400 "can't parse entities" | Mandatory escaping, snapshot tests for special characters |
| Secret leakage | Key appears in Actions logs | Never log headers or resolved config; read keys only from env |
| Repo growth from `data/` | Steady size increase | Each digest is a few tens of KB; `seen.json` prunes after 60 days |

---

## 9. Human-only setup

These steps require account access; the coding agent should remind the owner when a phase depends on them, not attempt them.

- [ ] Create the `tech-digest` repo (public if using free Pages), clone it, put this file at `docs/PLAN.md`.
- [ ] Create a bot via @BotFather; get `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
- [ ] Add secrets under Settings → Secrets and variables → Actions.
- [ ] Enable Pages: Settings → Pages → Source: GitHub Actions.
- [ ] Edit `profile` in `config.yaml` to match the owner's stack.
- [ ] Before phase 2: get Gemini (AI Studio) and Groq keys.
- [ ] After each phase: trigger the workflow manually with "Run workflow" and check Telegram.
