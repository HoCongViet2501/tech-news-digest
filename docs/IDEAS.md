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
