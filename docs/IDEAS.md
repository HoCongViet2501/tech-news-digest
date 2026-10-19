# Ideas and deferred work

Items outside the current plan, or deferred from it. Not scheduled.

## Reddit source (deferred from phase 1)

As of 2026-09-28 Reddit's anonymous JSON endpoints no longer work for us:
`old.reddit.com/r/{sub}/top.json` redirects to `/login`, `api.reddit.com` returns 403,
and `www.reddit.com` is DNS-blocked on the owner's network. Reddit is disabled in
`config.yaml` and has no fetcher, fixture or offline route.

Options if it is revived:
- Official OAuth API (script app, client-credentials flow): real scores, needs
  `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` secrets and owner account setup.
- Subreddit RSS (`/r/{sub}/top/.rss?t=day`): no key, but no upvote score, so
  `min_score` cannot be applied; would need a `base_score` like other RSS feeds.

## Local embedding scorer (optional extra from phase 2 step 7)

Not implemented. An `embed` dependency group with sentence-transformers
(`paraphrase-multilingual-MiniLM-L12-v2`) could score titles by cosine similarity
to the profile when every LLM provider fails, instead of falling back to the
heuristic. Fully local and keyless, but adds a large dependency and model download
to every Actions run.

## Radar extras (deferred from phase 3)

- One-time "you are behind a major" report on the first run. Today the first sighting
  of a repo is a silent baseline, so a major that already existed is never mentioned.
- More manifest formats: Poetry (`[tool.poetry.dependencies]`), lock files
  (`package-lock.json`, `uv.lock`, `go.sum`) for exact installed versions instead of
  the lower bound of a range.
- Monorepos publishing many packages from one repo (babel, DefinitelyTyped): match
  release tags like `@scope/pkg@1.2.3` to the right package instead of reading every
  release of the repo.
- Radar entries in `feed.xml` (today only Telegram and the web pages show them).
