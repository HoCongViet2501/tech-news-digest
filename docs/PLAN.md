# Tech News Digest — Implementation Plan

A daily, personalized tech news digest. Every morning at 07:00 (Asia/Ho_Chi_Minh), a GitHub Actions job collects stories from 5–6 sources, filters them down to at most 10 items that match the owner's profile, sends them to Telegram, and archives them on GitHub Pages with an RSS feed. Running cost: zero.

Work is split into 4 phases. Each phase ends with a working, shippable version; the project can stop after any phase.

**Part 2 (sections 10–18)** extends the project into a multi-user web app in the style of daily.dev, in phases W0–W5. Owner decisions of 2026-11-14: Phase 3 (radar) is removed in W0, Phase 4 is dropped, and running cost must **always** stay at $0.

---

## 1. Goals, scope and key decisions

**Goals**

- The digest can be read in ~3 minutes: max 10 items per day.
- Never resend an item that was sent on a previous day.
- No server, no paid component.
- Every phase ends in a runnable state.

**Out of scope (Part 1):** a custom web/app with login, multi-user support, realtime updates. Part 2 brings the web app and invite-only multi-user support into scope; realtime updates stay out.

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

> **Removed in W0** (owner decision 2026-11-14: not needed for the web version). The last commit containing the radar is `823a006`; see `docs/IDEAS.md`.

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

> **Dropped** (owner decision 2026-11-14). Web feedback (W4) replaces the Telegram buttons and polling; the weekly digest moves to W5. Kept below for reference only; do not implement.

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

---

# Part 2 — Tech News Web (phases W0–W5)

A multi-user web app in the style of daily.dev. The system collects stories from many sources and tags them against a shared taxonomy. Each user gets a personal "My Feed" ranked by their focus, which it learns from behavior (click, upvote, bookmark, hide). The daily Telegram digest stays, as the owner's top 10 from My Feed.

## 10. Owner decisions (2026-11-14)

| # | Decision |
| --- | --- |
| 1 | Multi-user schema from the start; sign-up is **invite-only** (email allowlist). |
| 2 | Stack: Next.js + Supabase (Postgres, pgvector, Auth) on Vercel; the Python pipeline stays for ingestion. |
| 3 | Running cost must **always be $0**. Every component runs on a free tier. When a free limit is near, degrade (shorter retention, fewer LLM calls, fewer runs) and never upgrade to a paid plan. |
| 4 | Old Phase 4 (Telegram feedback buttons + polling) is dropped. |
| 5 | Phase 3 radar is removed from the codebase in W0. |
| 6 | The engineering-blog RSS list and the ~60-tag taxonomy are drafted by the agent and approved by the owner at the start of W1 / W2. |

## 11. Feasibility study (2026-11-14)

### Sources (probed once each, no keys, generic User-Agent)

| Source | Endpoint | Items | Popularity signal | Native tags | Priority |
| --- | --- | --- | --- | --- | --- |
| Hacker News | Algolia `search?tags=front_page`, `show_hn` | 30–50 | points, comments | – | In use |
| Lobsters | `hottest.json` | 25 | score | yes | In use |
| GitHub Trending | HTML | ~25 | stars today | language | In use |
| dev.to | `api/articles?top=1`, `?tag=` | 30/page | reactions, comments | yes (`tag_list`) | High |
| Hugging Face Papers | `api/daily_papers` | 50 | upvotes | `ai_keywords`, `ai_summary` | High (AI) |
| Engineering blogs RSS | GitHub, Cloudflare, AWS, Kubernetes, InfoQ, Changelog, … | 15–185/feed | none | category (some) | High |
| GitHub Search | `search/repositories?q=created:>D&sort=stars` | 30/page | stars | `topics` | Medium (needs spam filter) |
| HF trending models | `api/models?sort=trendingScore` | 20 | trendingScore | pipeline tag | Medium |
| Viblo (Vietnamese) | `/rss`, `/api/posts/trending` | 40 | in API | yes | Medium |
| Product Hunt | Atom `/feed` | 50 | none (votes need a GraphQL token) | – | Low |
| Echo JS | `/rss` | 30 | none | – | Low |
| Lemmy programming.dev | `api/v3/post/list?sort=TopDay` | 20 | score | community | Low |
| Stack Overflow | `api.stackexchange.com …sort=hot` | 30 | score | yes | Low (Q&A, 300 req/day without key) |
| Medium tag RSS | `/feed/tag/{tag}` | 10 | none | tag | Low (paywalls) |
| YouTube channel RSS | `feeds/videos.xml?channel_id=` | 15 | none | – | Optional |
| arXiv, Mastodon tags, TLDR RSS | – | – | none / very low | – | Not used |

Blocked: Reddit (JSON and RSS both redirect to a login wall), Bluesky search (403), daily.dev (no public API, persisted GraphQL queries only; use it as a UX reference, **never scrape it**), Hashnode (`gql.hashnode.com` → 301, endpoint unconfirmed), X/Twitter (paid API).

### Embedding experiment

390 real items from 16 sources, embedded locally with `fastembed` on CPU (~40 items/s).

| Model | Ranking by profile | Zero-shot tags | Vietnamese |
| --- | --- | --- | --- |
| `bge-small-en-v1.5` | Clear separation (top ~0.71: K8s, MCP, Bedrock agents; bottom ~0.3: FAA, Meta fine) | Good for AI / k8s / security; wrong for Go and Python | Poor |
| `paraphrase-multilingual-MiniLM-L12-v2` | Equally good separation | Similar, lower scores | Good |

Takeaways:
1. Embeddings work for coarse filtering and similarity, but they **ignore negation**: the profile says "not interested in hiring", yet hiring posts still reached the top 5. Explicit signals (block, hide, "less like this") must handle the negative side.
2. Zero-shot tagging is only reliable for broad topics. Language and tool tags need native tags and alias rules.
3. Use the multilingual model.
4. Embedding 1000 items takes ~25 s on an Actions runner, with no GPU and no paid API.

## 12. Feature scope (reference: daily.dev)

In scope:
- **W3:** card grid feed (cover, title, source, tags, reading time), Popular / Most upvoted / Discussed, full-text search, AI TL;DR in `output_language`.
- **W4:** My Feed from followed tags, onboarding, upvote / bookmark / hide / "less like this", block tag or source, reading history.

Out of scope, recorded in `IDEAS.md`: comments, squads, public profiles, reputation, the browser new-tab extension.

## 13. Architecture

```
                    GitHub Actions cron (every 3h)                   Vercel (Hobby)
┌──────────────────────────────────────────────────────┐    ┌──────────────────────┐
│ Python pipeline (src/digest)                         │    │ Next.js (App Router) │
│ fetchers → normalize → dedupe (URL + near-dup)       │    │  /, /popular, /tags  │
│   → enrich (og:image, text, reading time, language)  │    │  /my-feed, /bookmarks│
│   → tag (native → rule → embed → LLM)                │    │  /search, /onboarding│
│   → embed (fastembed, 384d) → quality score (LLM)    │    └─────────┬────────────┘
│   → TL;DR for the popular pool → upsert Postgres     │              │ supabase-js + RPC
│ daily cron: user vectors, Telegram digest            │              │
└──────────────────────┬───────────────────────────────┘              │
                       ▼                                              ▼
              ┌──────────────────────────────────────────────────────────┐
              │ Supabase free: Postgres + pgvector + Auth (GitHub) + RLS │
              └──────────────────────────────────────────────────────────┘
```

| Area | Choice | Why |
| --- | --- | --- |
| Ingestion | Python, existing code | Fetchers, LLM chain and tests already exist |
| Schedule | Actions cron `0 */3 * * *` | Free for public repos |
| DB | Supabase Postgres + `pgvector` (free: 500 MB) | Auth + RLS included; ranking is one SQL query |
| Web | Next.js + TypeScript + Tailwind on Vercel Hobby. Public pages are cached (`"use cache"` + `cacheLife`). Personalized data (My Feed, bookmarks, events) is fetched **from the browser straight to Supabase RPC** with the user's JWT, never through Vercel functions. | Matches the owner's stack; keeps Vercel function usage near zero and keeps a later move to a static export (e.g. Cloudflare Pages) easy |
| Auth | Supabase Auth, GitHub OAuth; allowlist enforced by a "before user created" Auth Hook in the DB | No password storage; the allowlist cannot be bypassed by calling Auth directly |
| Embeddings | `fastembed` + `paraphrase-multilingual-MiniLM-L12-v2`, model cached with `actions/cache` | Local, keyless, handles Vietnamese |
| LLM | Existing provider chain | Free tiers with fallback |

Repo layout becomes a monorepo: `src/digest/` (pipeline), `web/` (Next.js), `supabase/` (Supabase CLI: `config.toml`, `migrations/`; the CLI cannot move migrations elsewhere).

### Deployment

| Part | How it deploys | Secrets / env |
| --- | --- | --- |
| Database | SQL in `supabase/migrations/`. `db.yml` runs `supabase db push` on merges to `main` that touch that folder, before any other job. | `SUPABASE_DB_URL` (Actions only). `db push --remote=<db url>` needs no Supabase access token. |
| Pipeline | `ingest.yml` every 3 h. `daily.yml` at 07:00 handles Telegram, user vectors and cleanup. Both share one `concurrency` group so runs never overlap. Writes go through psycopg via the Supavisor pooler (IPv4; the direct host is IPv6-only and Actions runners have no IPv6). Session mode on port 5432. | `SUPABASE_DB_URL`, LLM keys, `TELEGRAM_*` |
| Web | Vercel Git integration, root `web/`. `main` deploys production; PRs get preview deploys. An "Ignored Build Step" skips builds when `web/` did not change, so daily `digest:` commits cost no builds. | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (publishable key). No secret key exists in the project setup at all; the web always runs as the user (JWT + RLS). |
| CI | `ci.yml`: pytest + ruff, `pnpm lint/test/build`. SQL and RLS tests run against a Postgres service container on the runner. | – |

- **Environments:** local (`supabase start`, Docker) and production only. There is no staging project: a second free project pauses after 7 days idle. Preview deploys read production through the anon key, so RLS limits them to public data.
- **GitHub Pages:** stays until W3 ships, then becomes a redirect to the web app. `data/` (`seen.json`, digests) is still committed daily, because that activity keeps scheduled workflows enabled.

### Ingestion

Sources are configured in `sources.yaml` and synced into the `sources` table. There are three fetcher kinds:
- **Generic RSS** (`rss.py`): engineering blogs, Viblo, Product Hunt, Echo JS, YouTube. A new feed is one config line.
- **One module per API source**: HN, Lobsters, dev.to, HF Papers, GitHub Search, Lemmy. Each follows the `FetchFn` protocol.
- **HTML scraping**: GitHub Trending.

Each source has a `cadence` so quotas are not burned on all 8 runs a day: HN, Lobsters and dev.to every run; GitHub Search twice a day; HF Papers and blog RSS once a day.

One `ingest.yml` run (target ~5 min, job timeout 20 min):
1. **Fetch** sources that are due, concurrently (8 in flight, at most 2 per host). Conditional GET with `ETag` / `Last-Modified` stored on `sources`; a 304 is skipped.
2. **Normalize** to canonical URL → item id.
3. **Upsert**: new items are inserted; known items only refresh `native_score` / `native_comments`, so popularity keeps up with growing HN points.
4. **Enrich** new items only: `og:image`, description, reading time, language. Uses trafilatura with a 10 s timeout and at most `ingest.enrich_per_run` items; failures leave nulls.
5. **Cluster** near-duplicates over 72 h.
6. **Tag and embed** items that lack them.
7. **LLM** tag fallback + quality, batches of 20, capped by `ingest.llm_calls_per_run`.
8. **Recompute** `pop_pct` per source over 7 days.
9. **TL;DR** for popular-pool items without one.
10. **Health:** update `sources.last_ok_at` / `last_error`; a source failing for > 24 h triggers a Telegram warning.

**Postgres is the work queue.** Each stage selects only items whose stage column (`enriched_at`, `tagged_at`, `embedded_at`, `summarized_at`) is null. A crashed or quota-limited run is finished by the next one, and every write is an upsert on `items.id`, so reruns never duplicate.

**Content and politeness rules:**
- Store and show only the title, a short description excerpt, links, the image URL and our own TL;DR.
- Full article text is used transiently for embeddings and TL;DR, and is never stored or displayed.
- Enrichment respects `robots.txt`, uses the project User-Agent, and only fetches pages of new items.

**Tests:**
- Fetchers keep the fixture + respx tests.
- DB access goes through a repository interface: an in-memory implementation for unit tests, and a Postgres service container for integration tests.
- `DIGEST_OFFLINE=1` reads fixtures and writes to local Supabase.

## 14. Data model (Postgres)

```sql
sources(id, kind, name, url, enabled, base_weight, cadence_hours, etag, last_modified,
        last_fetched_at, last_ok_at, last_error)
items(id text pk /*sha1 canonical url*/, cluster_id, source_id, title, url, discussion_url,
      description, image_url, lang, reading_min, published_at, fetched_at,
      native_score int, native_comments int, pop_pct real /*per-source percentile, 7 days*/,
      quality real /*0–1, LLM*/, tldr text, embedding halfvec(384), fts tsvector,
      first_seen_at, enriched_at, tagged_at, embedded_at, summarized_at /*stage queue*/)
clusters(id, canonical_item_id, item_ids text[])
tags(slug pk, name, parent, description, aliases text[])        -- seeded from taxonomy.yaml
item_tags(item_id, tag, confidence real, method /*native|rule|embed|llm*/)
invites(id, email null, github_login null, invited_at, used_at)  -- match by email OR GitHub login
profiles(user_id pk, display_name, lang, profile_text, profile_embedded_at, user_vec halfvec(384),
         model jsonb, updated_at)
user_tags(user_id, tag, state /*follow|block*/)
user_sources(user_id, source_id, state /*block*/)
events(id, user_id, item_id, kind /*click|upvote|downvote|bookmark|hide|less_like_this*/,
       dwell_ms, created_at)
impressions(id, user_id, snapshot_id, item_ids text[], created_at)  -- one row per page view, not per card
feed_snapshots(id, user_id, item_ids text[], created_at)            -- ranked list, kept 30 min
tag_affinity(user_id, tag, score real, updated_at)
```

Embeddings use `halfvec(384)` (~0.8 KB instead of ~1.5 KB). There is **no ANN index** (HNSW): the 7-day pool (~7k items) is scanned exactly in a few ms, and an HNSW index over 90 days would take RAM the free instance does not have. Full-text search uses the `simple` config + `unaccent` (Postgres has no Vietnamese config), plus a `pg_trgm` index on titles for typo tolerance.

Storage: ~1000 items/day × ~2.8 KB ≈ 85 MB/month. Embeddings and descriptions are kept for `retention.content_days` (default 90), then only metadata is kept. A weekly cleanup job keeps the DB under 400 MB; database size is reported in `stats`.

## 15. Tagging and ranking

### Tagging (stop when confident)
1. **Native tags → aliases** (`k8s` → `kubernetes`), confidence 0.95.
2. **Keyword/regex rules** from `taxonomy.yaml`, 0.9.
3. **Embedding vs. tag description**, only for broad tags (`ai`, `devops`, `security`, `frontend`, `databases`, …), thresholds in config, 0.6–0.8.
4. **LLM**, only for items that pass the coarse filter and still have no tag ≥ 0.8. Runs in batches of 20 with JSON output and must pick **only** from the taxonomy. The same call scores `quality` (technical depth, user-independent). Prompt: `ai/prompts/tag.md`.

Target: precision ≥ 0.85 and recall ≥ 0.70 on 200 owner-labeled items.

### Dedupe
Canonical URL, plus near-duplicates: title cosine ≥ 0.92 within 72 h → same cluster. A card shows "also on HN · Lobsters", and popularity is combined across sources.

### Per-user score (one plpgsql function, used by both the web and Telegram)
```
interest   = 0.5·tag_match(u,i) + 0.5·cos(user_vec, item_vec)
popularity = pop_pct + bonus when the cluster spans ≥2 sources
freshness  = exp(-age_hours / 36)
score      = 0.40·interest + 0.25·popularity + 0.20·quality + 0.15·freshness
```
1. **Filter:** drop blocked tags/sources, hidden items and items already clicked.
2. **Score candidates:** score the 7-day pool and keep the top 200.
3. **Diversify:** run MMR (λ = 0.7) over those 200, allow at most 3 items per source per page, and fill 10–15% of slots with "explore" items from Popular.
4. **Snapshot:** store the ranked ids in `feed_snapshots`. The first page view computes the snapshot. Later pages slice it by offset, which keeps pagination stable and costs no re-ranking. A snapshot older than 30 minutes, or a pull-to-refresh, triggers a new one.

All weights live in `config.yaml` under `ranking:`; the pipeline syncs them into a `ranking_config` table that the function reads. The whole ranking lives in SQL only, so Python and TypeScript never duplicate it.

**Security:**
- `my_feed(cursor)` takes the user from `auth.uid()` and never accepts a user id from the client.
- `feed_for_user(user_id, n)` exists only for the Telegram digest and is granted to `service_role` alone.

### Learning the user's focus (no model fine-tuning)
- **Cold start:** onboarding asks for ≥3 tags plus optional free text.
  - The web cannot run the embedding model. The free text is embedded by the next `ingest.yml` run: rows with `profile_embedded_at` null, at most 3 h later.
  - Until then, `user_vec` = centroid of the chosen tags, and interest relies on `tag_match`.
  - Afterwards, `user_vec` = embedding of the text + centroid of the chosen tags.
- **Online learning:**
  - Event weights: click +1, dwell ≥30 s +2, upvote +3, bookmark +4, hide −3, downvote −4, less_like_this −5. Half-life 21 days.
  - `tag_affinity[t] = tanh(Σ w·decay)`
  - `user_vec = normalize(α·init_vec + Σ w·decay·item_vec)`
  - Recomputed daily; hide and block apply immediately.
- **Supervised (W5):**
  - Applies to users with ≥150 explicit events.
  - Train a logistic regression daily on `[cos, tag_match, pop_pct, quality, freshness, source]`.
  - Run it in shadow mode. Enable it only when offline NDCG@10 beats the fixed formula by ≥5%.

### LLM budget
TL;DR for ~100 items/day (batches of 10). Tags + quality for ~200–300 items/day (batches of 20). That is about 25–40 calls/day in total, with **no LLM call per user or per request**. If every provider fails, tagging falls back to native/rule/embed and TL;DR is skipped.

## 16. Phases

Every phase ends in a runnable state. All Part 1 conventions in `CLAUDE.md` still apply.

### W0 — Groundwork
1. Merge this plan into `PLAN.md` (done 2026-11-14).
2. Remove the radar:
   - Delete `src/digest/radar/`, its tests and fixtures, the snapshots, the `radar:` config section, the radar env in `daily.yml`, and the Radar block in the templates.
   - Note in `IDEAS.md` that commit `823a006` has the last version.
3. Monorepo skeleton:
   - `web/`: Next.js + TypeScript + Tailwind, pnpm.
   - `supabase/` (via `supabase init`) and the `db.yml` workflow.
   - CI runs `pytest`, ruff, plus `pnpm lint`, `pnpm test` and `pnpm build`.
   - Document the Vercel "Ignored Build Step" command (§13 Deployment).
   - TypeScript DB types generated with `supabase gen types typescript`; CI fails when the committed types differ from the migrations.
   - Playwright set up against local Supabase (`supabase start`) in CI; flows are added in W3/W4.
4. **Acceptance:** CI green; the daily Telegram digest still runs unchanged.

### W1 — Content store
1. Postgres schema (§14) and migrations. Postgres storage backend in `state.py`; `seen.json` stays for Telegram.
2. Owner approves the engineering-blog RSS list (~30 feeds) in `sources.yaml`.
3. New fetchers: `devto`, `hf_papers`, `github_search` (spam filter), `lemmy`, `viblo`, `producthunt_rss`. Each with a fixture and a test.
4. Per-source `cadence_hours` and conditional GET (`ETag` / `Last-Modified`).
5. Enrichment: `og:image`, reading time, language detection. Respect `robots.txt`; never store full text.
6. Stage columns as the work queue (§13 Ingestion); per-run caps in `config.yaml` (`ingest.enrich_per_run`, `ingest.llm_calls_per_run`).
7. Near-duplicate clustering.
8. `ingest.yml` every 3 h with a shared `concurrency` group; source health via `sources.last_ok_at` / `last_error`, Telegram warning after 24 h of failures.
9. **Acceptance:**
   - ≥500 unique items/day.
   - One dead source does not break the run.
   - Two consecutive runs create no duplicates.
   - A run killed midway is completed by the next run.
   - A second fetch of an unchanged feed gets 304.

### W2 — Tags and embeddings
1. Owner approves `taxonomy.yaml` (~60 tags, aliases, descriptions); seed `tags`.
2. Four-step tagger (§15), fastembed with the cached model, `ai/prompts/tag.md`.
3. `scripts/label_sample.py` exports 200 items for labeling; `scripts/eval_tags.py` reports precision/recall.
4. **Acceptance:** precision ≥ 0.85 and recall ≥ 0.70 (**owner labels manually**); 1000 items embedded in < 60 s on Actions.

### W3 — Public web MVP (no login)
1. Pages:
   - Popular (card grid) and tag pages.
   - Item page: TL;DR, original link, discussion link, "also on".
   - Full-text search (`simple` + `unaccent` + `pg_trgm`, §14).
   - Dark mode and mobile layout.
   - Virtualized infinite-scroll list; the next page is prefetched.
   - Images: no `next/image` optimization (Hobby quota). Thumbnails go through the free wsrv.nl resize proxy, with `loading="lazy"` and a fixed aspect ratio. A broken image falls back to the source favicon on a gradient.
2. TL;DR generated in the pipeline for the popular pool.
3. Cached public pages (15 min) to save DB and Vercel quota. Next 16 runs with `cacheComponents: true`, so this is `"use cache"` + `cacheLife`, not the `revalidate` segment config.
4. GitHub Pages becomes a redirect to the web app.
5. **Acceptance:** Lighthouse mobile ≥ 90; tag pages show only matching items; searching "kien truc" finds "kiến trúc"; Playwright covers browsing Popular, a tag page and search; snapshot tests for cards with `& < >` and emoji; **owner reviews the UI manually**.

### W4 — Accounts and personalization v1
1. Supabase Auth (GitHub OAuth) with the "before user created" hook checking `invites` by email or GitHub login. RLS on `profiles`, `user_*`, `events`, `impressions`, `feed_snapshots`, `tag_affinity`.
2. Onboarding: tags plus optional free text (embedded asynchronously, §15).
3. My Feed: `my_feed(cursor)` (§15), called from the browser through Supabase RPC; snapshots for pagination.
4. Actions:
   - User actions: upvote, bookmark, hide, less-like-this, block tag/source.
   - Tracking: one `impressions` row per page view; click and dwell events batched and sent with `sendBeacon`.
5. Bookmarks and reading-history pages; a "delete my history" action.
6. The Telegram digest becomes the owner's My Feed top 10 via `feed_for_user`, linking to the web.
7. **Acceptance:** two fixture users with different tags get different feeds; hide/block take effect on the next page; RLS test shows user A cannot read user B's events or snapshots; calling `feed_for_user` with the anon key fails; a non-invited account cannot be created even by calling Auth directly; paging through 5 pages shows no duplicates; Playwright covers sign-in, onboarding, hide and bookmark; **owner uses it for 7 days and rates ≥7/10 of the top items worth reading**.

### W5 — Learning from behavior
1. Daily job recomputes `tag_affinity` and `user_vec` (§15).
2. Per-user logistic regression in shadow mode; offline NDCG@10 script over `events`.
3. "Related items" on the item page (pgvector, from the item's own embedding, so nothing is embedded at request time); a "why you see this" note (matched tags, similarity). Free-text semantic search is not planned: the web cannot embed queries with the same model.
4. Weekly digest on Sunday 08:00 Vietnam time with stats, plus at most 3 tag/profile suggestions (suggest only, never auto-apply).
5. **Acceptance:** after 2 weeks My Feed CTR is higher than Popular (owner checks); "less like this" visibly reduces same-tag items within 3 days; the model is only enabled when it beats the baseline.

## 17. Risks ($0 constraint)

| Risk | Mitigation |
| --- | --- |
| Supabase free project pauses after 7 days without activity | Ingest every 3 h keeps it active; warn when ingest has failed for > 24 h |
| DB exceeds 500 MB | Retention, weekly cleanup, size in `stats`; shorten retention before anything else |
| Actions schedules are disabled after 60 days without repo activity | Daily digest commits keep the repo active; keep that commit step |
| Vercel Hobby limits (non-commercial, bandwidth, function time, image optimization) | Personal, non-commercial, invite-only use; cached public pages; personalized data goes browser → Supabase; no `next/image` optimization; never upgrade. Fallback: static export to Cloudflare Pages |
| GitHub Search spam repos | Filter by account age, fork/star ratio, missing description; low source weight |
| RSS sources lack popularity signals | Per-source `base_weight`, LLM quality, cross-source cluster bonus |
| LLM free tier tightened | Provider chain; tagging works without LLM; TL;DR optional |
| Filter bubble | MMR plus explore slots |
| User privacy | RLS, never log user ids or emails in Actions, history deletion |

## 18. Human-only setup (Part 2)

- [ ] Create a Supabase project (free, region Singapore). Extensions (`vector`, `unaccent`, `pg_trgm`) are enabled by migrations, not by hand.
  - Actions secret: `SUPABASE_DB_URL` (session pooler, port 5432) only. No Supabase access token is needed (verified 2026-11-15 with CLI 3.1.0 `db push --remote=<url> --dry-run`).
  - Web (W3): project URL and the publishable (anon) key.
  - No secret/service-role key is needed: the pipeline connects as Postgres through `SUPABASE_DB_URL`, which can call service-only functions.
- [ ] Create a GitHub OAuth App for Supabase Auth.
- [ ] Create a Vercel Hobby project for `web/`; add env vars.
- [ ] Add the new secrets to GitHub Actions.
- [ ] Approve the RSS list (W1) and the taxonomy (W2); label 200 items (W2).
- [ ] Add invited emails; use and rate the feed in W4/W5.
