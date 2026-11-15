-- W4 accounts and personalization v1 (docs/PLAN.md §14 user tables, §15 per-user score,
-- §16 W4).
--
-- Access model (extends the W1 content store):
--   * Clients (`authenticated`) may SELECT their own rows of the user tables, through
--     column grants and RLS. They never write a table directly: every write goes through
--     a SECURITY DEFINER RPC below that takes the user from auth.uid() and validates the
--     input. So a client can never set user_vec, model, tag_affinity, snapshots or
--     someone else's rows.
--   * `invites` and `ranking_config` have no client grants at all. The Auth hook reads
--     invites; the ranking reads ranking_config through private.ranking().
--   * `anon` gets nothing new except EXECUTE on private.ranking() (popular_feed, which
--     anon calls, needs the weights). The `private` schema is not exposed by the Data
--     API (supabase/config.toml [api] schemas), so nothing in it is reachable over REST.
--   * The pipeline connects as `postgres` and the Auth server as `supabase_auth_admin`.
--   * feed_for_user (Telegram digest) is for service_role / postgres only.

create schema if not exists private;
revoke all on schema private from public;
grant usage on schema private to anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- ranking_config: one row with every ranking knob (§15). Synced from config.yaml
-- `ranking:` at the start of each ingest run (PostgresStore.sync_ranking). The
-- defaults are the §15 values, so the ranking works before the first sync.
-- ---------------------------------------------------------------------------
create table public.ranking_config (
  id                    boolean primary key default true check (id),
  -- score = w_interest·interest + w_popularity·popularity + w_quality·quality + w_freshness·freshness
  w_interest            double precision not null default 0.40 check (w_interest between 0 and 1),
  w_popularity          double precision not null default 0.25 check (w_popularity between 0 and 1),
  w_quality             double precision not null default 0.20 check (w_quality between 0 and 1),
  w_freshness           double precision not null default 0.15 check (w_freshness between 0 and 1),
  -- interest = t·tag_match + (1 - t)·cos(user_vec, item); t = interest_tag_weight, or
  -- cold_start_tag_weight while user_vec is still the followed-tags centroid.
  interest_tag_weight   double precision not null default 0.5 check (interest_tag_weight between 0 and 1),
  cold_start_tag_weight double precision not null default 0.7 check (cold_start_tag_weight between 0 and 1),
  affinity_weight       double precision not null default 0.5 check (affinity_weight between 0 and 1),
  multi_source_bonus    double precision not null default 0.2 check (multi_source_bonus between 0 and 1),
  freshness_hours       double precision not null default 36 check (freshness_hours > 0),
  default_quality       double precision not null default 0.5 check (default_quality between 0 and 1),
  pool_days             integer not null default 7 check (pool_days between 1 and 30),
  centroid_days         integer not null default 7 check (centroid_days between 1 and 90),
  candidates            integer not null default 200 check (candidates between 10 and 500),
  mmr_lambda            double precision not null default 0.7 check (mmr_lambda between 0 and 1),
  page_size             integer not null default 24 check (page_size between 1 and 60),
  max_per_source        integer not null default 3 check (max_per_source >= 1),
  explore_ratio         double precision not null default 0.125 check (explore_ratio between 0 and 0.5),
  snapshot_minutes      integer not null default 30 check (snapshot_minutes > 0),
  updated_at            timestamptz not null default now(),
  constraint ranking_config_popular_weights check (w_popularity + w_quality + w_freshness > 0)
);
insert into public.ranking_config default values;

-- The single config row. SECURITY DEFINER so callers need no grant on the table.
create function private.ranking()
returns public.ranking_config
language sql
stable
security definer
set search_path = ''
as $$
  select r from public.ranking_config r where r.id
$$;

-- ---------------------------------------------------------------------------
-- invites: the sign-up allowlist, matched by email OR GitHub login, case-insensitively.
-- Owner-managed (SQL editor / psql); no client access.
-- ---------------------------------------------------------------------------
create table public.invites (
  id           bigint generated always as identity primary key,
  email        text check (email ~ '^[^@\s]+@[^@\s]+$'),
  github_login text check (github_login ~ '^[A-Za-z0-9][A-Za-z0-9-]{0,38}$'),
  invited_at   timestamptz not null default now(),
  used_at      timestamptz,
  constraint invites_email_or_login check (email is not null or github_login is not null)
);
create unique index invites_email_key on public.invites (lower(email)) where email is not null;
create unique index invites_github_login_key on public.invites (lower(github_login))
  where github_login is not null;

-- ---------------------------------------------------------------------------
-- profiles: one row per auth user, created by a trigger on auth.users.
--   user_vec / model / profile_embedded_at are written by the pipeline only.
--   Changing profile_text clears profile_embedded_at, so ingest re-embeds it (§15).
-- ---------------------------------------------------------------------------
create table public.profiles (
  user_id             uuid primary key references auth.users (id) on delete cascade,
  display_name        text check (char_length(display_name) <= 100),
  lang                text not null default 'vi' check (lang ~ '^[a-z]{2}$'),
  profile_text        text check (char_length(profile_text) <= 2000),
  profile_embedded_at timestamptz,
  user_vec            extensions.halfvec(384),
  model               jsonb,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);
-- Ingest queue: profiles whose free text still needs an embedding.
create index profiles_pending_embed_idx on public.profiles (updated_at)
  where profile_embedded_at is null and profile_text is not null;

create function private.profiles_touch()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  if new.profile_text is distinct from old.profile_text then
    new.profile_embedded_at := null;
  end if;
  return new;
end;
$$;

create trigger profiles_touch before update on public.profiles
  for each row execute function private.profiles_touch();

-- ---------------------------------------------------------------------------
-- user_tags (follow | block), user_sources (block).
-- ---------------------------------------------------------------------------
create table public.user_tags (
  user_id    uuid not null references auth.users (id) on delete cascade,
  tag        text not null references public.tags (slug) on update cascade on delete cascade,
  state      text not null check (state in ('follow', 'block')),
  created_at timestamptz not null default now(),
  primary key (user_id, tag)
);
create index user_tags_tag_idx on public.user_tags (tag);

create table public.user_sources (
  user_id    uuid not null references auth.users (id) on delete cascade,
  source_id  bigint not null references public.sources (id) on delete cascade,
  state      text not null default 'block' check (state = 'block'),
  created_at timestamptz not null default now(),
  primary key (user_id, source_id)
);
create index user_sources_source_idx on public.user_sources (source_id);

-- ---------------------------------------------------------------------------
-- events: the behavior log (§15 Learning). Reactions (upvote, downvote, bookmark,
-- hide, less_like_this) are state: at most one row per (user, item, kind), and undoing
-- one deletes the row, so its learning weight disappears too. click and dwell are
-- append-only; dwell carries dwell_ms (the time away on the article).
-- ---------------------------------------------------------------------------
create table public.events (
  id         bigint generated always as identity primary key,
  user_id    uuid not null references auth.users (id) on delete cascade,
  item_id    text not null references public.items (id) on delete cascade,
  kind       text not null check (
               kind in ('click', 'dwell', 'upvote', 'downvote', 'bookmark', 'hide', 'less_like_this')
             ),
  dwell_ms   integer check (dwell_ms between 0 and 3600000),
  created_at timestamptz not null default now(),
  constraint events_dwell_only_on_dwell check ((kind = 'dwell') = (dwell_ms is not null))
);
create unique index events_reaction_key on public.events (user_id, item_id, kind)
  where kind in ('upvote', 'downvote', 'bookmark', 'hide', 'less_like_this');
create index events_user_created_idx on public.events (user_id, created_at desc);
create index events_item_idx on public.events (item_id);

-- ---------------------------------------------------------------------------
-- feed_snapshots: the ranked ids of one my_feed computation (§15 step 4). Pages slice
-- it by position. Fresh for ranking_config.snapshot_minutes; older ones are deleted
-- when the user's next snapshot is computed (cursors stay valid for 2 h).
-- impressions: one row per rendered page (§14).
-- tag_affinity: written by the W5 daily job; read by the ranking.
-- ---------------------------------------------------------------------------
create table public.feed_snapshots (
  id         bigint generated always as identity primary key,
  user_id    uuid not null references auth.users (id) on delete cascade,
  item_ids   text[] not null,
  created_at timestamptz not null default now()
);
create index feed_snapshots_user_created_idx on public.feed_snapshots (user_id, created_at desc);
create index feed_snapshots_created_idx on public.feed_snapshots (created_at);

create table public.impressions (
  id          bigint generated always as identity primary key,
  user_id     uuid not null references auth.users (id) on delete cascade,
  snapshot_id bigint references public.feed_snapshots (id) on delete set null,
  item_ids    text[] not null check (cardinality(item_ids) between 1 and 60),
  created_at  timestamptz not null default now()
);
create index impressions_user_created_idx on public.impressions (user_id, created_at desc);
create index impressions_snapshot_idx on public.impressions (snapshot_id) where snapshot_id is not null;

create table public.tag_affinity (
  user_id    uuid not null references auth.users (id) on delete cascade,
  tag        text not null references public.tags (slug) on update cascade on delete cascade,
  score      real not null check (score between -1 and 1),
  updated_at timestamptz not null default now(),
  primary key (user_id, tag)
);
create index tag_affinity_tag_idx on public.tag_affinity (tag);

-- ---------------------------------------------------------------------------
-- Row level security and grants for the user tables.
-- ---------------------------------------------------------------------------
alter table public.ranking_config enable row level security;
alter table public.invites enable row level security;
alter table public.profiles enable row level security;
alter table public.user_tags enable row level security;
alter table public.user_sources enable row level security;
alter table public.events enable row level security;
alter table public.feed_snapshots enable row level security;
alter table public.impressions enable row level security;
alter table public.tag_affinity enable row level security;

-- Default privileges already keep anon/authenticated out; stated again so this file is
-- safe on its own.
revoke all on table
  public.ranking_config, public.invites, public.profiles, public.user_tags, public.user_sources,
  public.events, public.feed_snapshots, public.impressions, public.tag_affinity
from anon, authenticated;
revoke all on sequence
  public.invites_id_seq, public.events_id_seq, public.feed_snapshots_id_seq, public.impressions_id_seq
from anon, authenticated;

-- profiles: everything except the pipeline's user_vec / model.
grant select (user_id, display_name, lang, profile_text, profile_embedded_at, created_at, updated_at)
  on table public.profiles to authenticated;
grant select on table
  public.user_tags, public.user_sources, public.events, public.feed_snapshots, public.impressions,
  public.tag_affinity
to authenticated;

create policy "Users read their own profile" on public.profiles
  for select to authenticated using (user_id = (select auth.uid()));
create policy "Users read their own tags" on public.user_tags
  for select to authenticated using (user_id = (select auth.uid()));
create policy "Users read their own sources" on public.user_sources
  for select to authenticated using (user_id = (select auth.uid()));
create policy "Users read their own events" on public.events
  for select to authenticated using (user_id = (select auth.uid()));
create policy "Users read their own snapshots" on public.feed_snapshots
  for select to authenticated using (user_id = (select auth.uid()));
create policy "Users read their own impressions" on public.impressions
  for select to authenticated using (user_id = (select auth.uid()));
create policy "Users read their own tag affinity" on public.tag_affinity
  for select to authenticated using (user_id = (select auth.uid()));

-- ---------------------------------------------------------------------------
-- Auth: invite-only sign-up ("before user created" hook, Postgres function).
--
-- Event: {"metadata": {...}, "user": {email, app_metadata: {provider}, user_metadata}}.
-- For GitHub, Supabase Auth (internal/api/provider/github.go) puts the login in
-- user_metadata as both `user_name` and `preferred_username`. user_metadata is
-- client-controlled on email/password sign-ups (signUp({options: {data}})), so the
-- login is trusted only when app_metadata.provider = 'github'.
-- The hook runs outside the transaction that inserts the user, so used_at can be set
-- for a sign-up that then fails; it is informational only and never blocks a retry.
-- ---------------------------------------------------------------------------
create function public.before_user_created_hook(event jsonb)
returns jsonb
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_user  jsonb := event -> 'user';
  v_email text := lower(nullif(btrim(v_user ->> 'email'), ''));
  v_login text;
begin
  if v_user -> 'app_metadata' ->> 'provider' = 'github' then
    v_login := lower(nullif(btrim(coalesce(
      v_user -> 'user_metadata' ->> 'user_name',
      v_user -> 'user_metadata' ->> 'preferred_username'
    )), ''));
  end if;

  update public.invites i
  set used_at = coalesce(i.used_at, now())
  where (v_email is not null and lower(i.email) = v_email)
     or (v_login is not null and lower(i.github_login) = v_login);

  if not found then
    return jsonb_build_object(
      'error', jsonb_build_object('http_code', 403, 'message', 'Sign-up is by invitation only.')
    );
  end if;
  return '{}'::jsonb;
end;
$$;

revoke all on function public.before_user_created_hook(jsonb)
  from public, anon, authenticated, service_role;
grant execute on function public.before_user_created_hook(jsonb) to supabase_auth_admin;

-- A profiles row for every new auth user (runs as supabase_auth_admin's insert).
create function private.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (user_id, display_name)
  values (
    new.id,
    left(coalesce(
      nullif(new.raw_user_meta_data ->> 'full_name', ''),
      nullif(new.raw_user_meta_data ->> 'user_name', ''),
      split_part(new.email, '@', 1)
    ), 100)
  )
  on conflict (user_id) do nothing;
  return new;
end;
$$;

create trigger on_auth_user_created after insert on auth.users
  for each row execute function private.handle_new_user();

-- Users that already exist (none in production; local dev accounts) get a profile too.
insert into public.profiles (user_id) select u.id from auth.users u on conflict do nothing;

-- ---------------------------------------------------------------------------
-- Popular pool and score, shared by popular_feed (W3) and the per-user ranking.
-- The W3 constants move to ranking_config (§15); the Popular order is unchanged with
-- the default config.
-- ---------------------------------------------------------------------------
drop function public.popular_score(real, bigint, real, timestamptz, timestamptz);

-- Popular score: the user-independent part of the §15 per-user score, renormalized so
-- its three weights sum to 1:
--   popularity = pop_pct + multi_source_bonus when the item (or cluster) spans >= 2 sites
--   quality    = LLM quality 0..1, default_quality when not scored yet
--   freshness  = exp(-age_hours / freshness_hours)
-- No `set search_path`: a function with a SET clause is never inlined, and this one runs
-- once per pool row (7k). It is SECURITY INVOKER and only uses pg_catalog builtins,
-- which are always resolved first.
create function public.popular_score(
  p_cfg public.ranking_config,
  p_pop_pct real,
  p_n_sites bigint,
  p_quality real,
  p_published_at timestamptz,
  p_as_of timestamptz
)
returns double precision
language sql
immutable
parallel safe
as $$
  select (
      (p_cfg).w_popularity * (coalesce(p_pop_pct, 0)
                              + case when coalesce(p_n_sites, 0) >= 2 then (p_cfg).multi_source_bonus else 0 end)
    + (p_cfg).w_quality * coalesce(p_quality, (p_cfg).default_quality)
    + (p_cfg).w_freshness * exp(
        -greatest(extract(epoch from (p_as_of - p_published_at)) / 3600.0, 0) / (p_cfg).freshness_hours
      )
  ) / ((p_cfg).w_popularity + (p_cfg).w_quality + (p_cfg).w_freshness)
$$;

-- One row per near-duplicate cluster (its canonical item, which must itself be in the
-- pool) published in the p_days before p_as_of and first seen by then, with the
-- cluster's popularity inputs. Was inlined in W3's popular_feed.
create function public.popular_pool(p_as_of timestamptz, p_days integer)
returns table (
  id           text,
  cluster_id   bigint,
  source_id    bigint,
  published_at timestamptz,
  pop_pct      real,
  quality      real,
  n_sites      bigint,
  upvotes      double precision,
  comments     double precision
)
language sql
stable
security invoker
set search_path = ''
as $$
  with
  pool as (
    select
      i.id,
      coalesce(c.canonical_item_id, i.id) as head,
      i.cluster_id,
      i.source_id,
      i.native_score,
      i.native_comments,
      i.pop_pct,
      i.quality,
      i.published_at
    from public.items i
    left join public.clusters c on c.id = i.cluster_id
    where i.published_at > p_as_of - make_interval(days => p_days)
      and i.first_seen_at <= p_as_of
  ),
  mention as (
    -- One row per (cluster, site): the best-scored mention.
    select distinct on (x.head, public.source_site(s.kind, s.id)) x.head, x.native_score, x.native_comments
    from (
      select p.head, im.source_id, im.native_score, im.native_comments
      from pool p
      join public.item_mentions im on im.item_id = p.id
      union all
      select p.head, p.source_id, p.native_score, p.native_comments
      from pool p
      where not exists (select 1 from public.item_mentions im where im.item_id = p.id)
    ) x
    join public.sources s on s.id = x.source_id
    order by x.head, public.source_site(s.kind, s.id), x.native_score desc
  ),
  stats as (
    select
      m.head,
      count(*) as n_sites,
      sum(m.native_score)::double precision as upvotes,
      sum(m.native_comments)::double precision as comments
    from mention m
    group by m.head
  ),
  grouped as (
    select p.head, max(p.pop_pct) as pop_pct, max(p.quality) as quality
    from pool p
    group by p.head
  )
  select h.id, h.cluster_id, h.source_id, h.published_at, g.pop_pct, g.quality,
         st.n_sites, st.upvotes, st.comments
  from grouped g
  join stats st on st.head = g.head
  join pool h on h.id = g.head
$$;

-- W3 popular_feed, now on popular_pool + ranking_config. Same signature and semantics.
create or replace function public.popular_feed(
  p_sort text default 'popular',
  p_tag text default null,
  p_as_of timestamptz default null,
  p_limit integer default 24,
  p_offset integer default 0
)
returns setof public.feed_card
language sql
stable
security invoker
set search_path = ''
as $$
  with recursive
  params as (
    select coalesce(p_as_of, now()) as as_of, private.ranking() as cfg
  ),
  tagset (slug) as (
    select t.slug from public.tags t where t.slug = p_tag and t.active
    union
    select t.slug from public.tags t join tagset ts on t.parent = ts.slug where t.active
  ),
  ranked as (
    select
      p.id,
      p.published_at,
      case p_sort
        when 'upvoted' then p.upvotes
        when 'discussed' then p.comments
        else public.popular_score(params.cfg, p.pop_pct, p.n_sites, p.quality, p.published_at, params.as_of)
      end as sort_key
    from params
    cross join lateral public.popular_pool(params.as_of, (params.cfg).pool_days) p
    where p_tag is null
       or exists (
            select 1 from public.item_tags it
            where it.item_id = p.id and it.tag in (select ts.slug from tagset ts)
          )
  ),
  page as (
    select r.id, row_number() over (order by r.sort_key desc nulls last, r.published_at desc, r.id) as rn
    from ranked r
    order by rn
    limit least(greatest(coalesce(p_limit, 24), 1), 60)
    offset greatest(coalesce(p_offset, 0), 0)
  )
  select c.*
  from public.feed_cards((select array_agg(page.id order by page.rn) from page)) c
$$;

-- ---------------------------------------------------------------------------
-- Per-user ranking internals (private: not callable over the Data API).
-- ---------------------------------------------------------------------------

-- Keeps the ids a user may see, in order: drops items the user hid or marked "less like
-- this" (and their near-duplicates), blocked sources, and items carrying a blocked tag
-- or any descendant of one. Used when ranking and again when a page is sliced from a
-- snapshot, so hide and block apply to the next page without re-ranking.
create function private.feed_filter(p_user uuid, p_ids text[])
returns table (id text, ord bigint)
language sql
stable
set search_path = ''
as $$
  with recursive
  blocked_tags (slug) as (
    select ut.tag from public.user_tags ut where ut.user_id = p_user and ut.state = 'block'
    union
    select t.slug from public.tags t join blocked_tags b on t.parent = b.slug
  ),
  hidden as (
    select e.item_id from public.events e
    where e.user_id = p_user and e.kind in ('hide', 'less_like_this')
  ),
  excluded (item_id) as (
    select h.item_id from hidden h
    union
    select g.id
    from hidden h
    join public.items hi on hi.id = h.item_id
    join public.items g on g.cluster_id = hi.cluster_id
    union
    select it.item_id from public.item_tags it join blocked_tags b on b.slug = it.tag
  )
  select u.id, u.ord
  from unnest(p_ids) with ordinality as u (id, ord)
  join public.items i on i.id = u.id
  where not exists (select 1 from excluded x where x.item_id = u.id)
    and not exists (
          select 1 from public.user_sources us
          where us.user_id = p_user and us.source_id = i.source_id
        )
$$;

-- Cold-start interest vector: the mean of the per-tag centroids of the user's followed
-- tags (each tag counts once, whatever its volume), where a tag's centroid is the mean
-- embedding of items tagged with it or a descendant in the last centroid_days. Tag
-- descriptions are never embedded in the DB, but item embeddings are, and they describe
-- what the tag looks like in this feed. Null when nothing matches.
create function public.follow_centroid(p_user uuid, p_as_of timestamptz default now())
returns extensions.halfvec
language sql
stable
set search_path = ''
as $$
  with recursive
  follows (root, slug) as (
    select ut.tag, ut.tag
    from public.user_tags ut
    join public.tags t on t.slug = ut.tag and t.active
    where ut.user_id = p_user and ut.state = 'follow'
    union
    select f.root, t.slug from follows f join public.tags t on t.parent = f.slug where t.active
  ),
  tagged as (
    select distinct f.root, i.id, i.embedding
    from follows f
    join public.item_tags it on it.tag = f.slug
    join public.items i on i.id = it.item_id
    where i.embedding is not null
      and i.published_at > p_as_of - make_interval(days => (private.ranking()).centroid_days)
  ),
  per_tag as (
    select extensions.l2_normalize(extensions.avg(t.embedding)) as c
    from tagged t
    group by t.root
  )
  select extensions.l2_normalize(extensions.avg(p.c)) from per_tag p
$$;

-- The ranked id list for one user (§15 steps 1–3):
--   1. Pool: popular_pool over pool_days, minus feed_filter and items (or clusters) the
--      user already clicked.
--   2. Score: w_interest·interest + (w_popularity + w_quality + w_freshness)·popular_score
--      (exactly the §15 sum, since popular_score is those three renormalized), where
--        interest  = t·tag_match + (1 - t)·max(cos(user_vec, item), 0)
--        tag_match = best confidence among the item's tags that the user follows (a
--                    followed tag covers its descendants: backend matches kafka),
--                    plus affinity_weight × the item's strongest positive and negative
--                    tag_affinity, clamped to 0..1.
--      Until profiles.user_vec exists, follow_centroid() stands in for it and
--      t = cold_start_tag_weight, so tag_match carries most of the interest.
--   3. Keep the top `candidates`, then MMR (λ = mmr_lambda; similarity = embedding
--      cosine) with at most max_per_source items per source in each page_size window.
--      Every round(1/explore_ratio)-th position is an explore slot: the best Popular
--      item outside the candidates.
create function private.rank_feed(p_user uuid, p_as_of timestamptz)
returns text[]
language plpgsql
stable
set search_path = ''
as $$
declare
  cfg       public.ranking_config := private.ranking();
  v_vec     extensions.halfvec;
  v_tag_w   double precision;
  ids       text[];
  rel       double precision[];
  src       integer[];             -- dense source index, 1..n_src
  ex_ids    text[];
  ex_src    integer[];
  n_src     integer;
  sims      double precision[];    -- n×n cosine matrix: sims[i][j]
  n         integer;
  n_ex      integer;
  k         integer;               -- explore period: positions k, 2k, ... (1-based)
  maxsim    double precision[];
  chosen    boolean[];
  ex_used   boolean[];
  per_src   integer[];
  out_ids   text[] := '{}';
  pos       integer := 0;
  prev      integer := 0;
  best      integer;
  best_val  double precision;
  capped    integer;               -- best item whose source is capped on this page
  capped_val double precision;
  val       double precision;
  lo        double precision;
  hi        double precision;
  pick_ex   integer;
  ii        integer;
  j         integer;
begin
  select p.user_vec into v_vec from public.profiles p where p.user_id = p_user;
  if v_vec is null then
    v_vec := public.follow_centroid(p_user, p_as_of);
    v_tag_w := cfg.cold_start_tag_weight;
  else
    v_tag_w := cfg.interest_tag_weight;
  end if;
  k := case when cfg.explore_ratio > 0 then greatest(round(1 / cfg.explore_ratio)::integer, 2) end;

  with recursive
  follows (slug) as (
    select ut.tag from public.user_tags ut
    where ut.user_id = p_user and ut.state = 'follow'
    union
    select t.slug from public.tags t join follows f on t.parent = f.slug
  ),
  pool as materialized (
    select p.*, public.popular_score(cfg, p.pop_pct, p.n_sites, p.quality, p.published_at, p_as_of) as pop
    from public.popular_pool(p_as_of, cfg.pool_days) p
  ),
  clicked as (
    select e.item_id from public.events e where e.user_id = p_user and e.kind = 'click'
  ),
  clicked_clusters as (
    select distinct i.cluster_id
    from clicked c join public.items i on i.id = c.item_id
    where i.cluster_id is not null
  ),
  eligible as (
    select p.*
    from pool p
    join private.feed_filter(p_user, (select array_agg(pool.id) from pool)) f on f.id = p.id
    where not exists (select 1 from clicked c where c.item_id = p.id)
      and not exists (select 1 from clicked_clusters cc where cc.cluster_id = p.cluster_id)
  ),
  scored as (
    select
      e.id,
      e.source_id,
      cfg.w_interest * (
          v_tag_w * tm.tag_match
        + (1 - v_tag_w) * greatest(coalesce(1 - extensions.cosine_distance(i.embedding, v_vec), 0), 0)
      )
      + (cfg.w_popularity + cfg.w_quality + cfg.w_freshness) * e.pop as score,
      e.pop,
      e.published_at
    from eligible e
    join public.items i on i.id = e.id
    cross join lateral (
      select least(1, greatest(0,
          coalesce(max(it.confidence) filter (where f.slug is not null), 0)
        + cfg.affinity_weight * (
              coalesce(max(greatest(ta.score, 0) * it.confidence), 0)
            + coalesce(min(least(ta.score, 0) * it.confidence), 0)
          )
      )) as tag_match
      from public.item_tags it
      left join follows f on f.slug = it.tag
      left join public.tag_affinity ta on ta.user_id = p_user and ta.tag = it.tag
      where it.item_id = e.id
    ) tm
  ),
  ranked as (
    select s.*, row_number() over (order by s.score desc, s.published_at desc, s.id) as r
    from scored s
  ),
  personal as (
    select * from ranked where r <= cfg.candidates
  ),
  explore as (
    select * from ranked
    where r > cfg.candidates and k is not null
    order by pop desc, published_at desc, id
    limit coalesce(cfg.candidates / (k - 1) + 1, 0)
  ),
  both_lists as (
    select id, source_id, score, r as ord, false as is_explore from personal
    union all
    select id, source_id, null, row_number() over (order by pop desc, published_at desc, id), true
    from explore
  ),
  dense as (
    select b.*, dense_rank() over (order by b.source_id)::integer as s from both_lists b
  )
  select
    array_agg(d.id order by d.ord) filter (where not d.is_explore),
    array_agg(d.score order by d.ord) filter (where not d.is_explore),
    array_agg(d.s order by d.ord) filter (where not d.is_explore),
    array_agg(d.id order by d.ord) filter (where d.is_explore),
    array_agg(d.s order by d.ord) filter (where d.is_explore),
    max(d.s)
  into ids, rel, src, ex_ids, ex_src, n_src
  from dense d;

  n := coalesce(cardinality(ids), 0);
  n_ex := coalesce(cardinality(ex_ids), 0);
  if n + n_ex = 0 then
    return out_ids;
  end if;

  -- Relevance normalized to 0..1 so λ trades it off against cosine similarity.
  if n > 0 then
    select min(x), max(x) into lo, hi from unnest(rel) x;
    for ii in 1 .. n loop
      rel[ii] := case when hi > lo then (rel[ii] - lo) / (hi - lo) else 1 end;
    end loop;

    -- Built row by row: one array_agg(... order by) over all n² values sorts them and
    -- is ~30x slower.
    with c as materialized (
      select u.ord, it.embedding
      from unnest(ids) with ordinality as u (id, ord)
      join public.items it on it.id = u.id
    )
    select array_agg(r.sim_row order by r.ord)
    into sims
    from (
      select a.ord, array(
        select coalesce(1 - extensions.cosine_distance(a.embedding, b.embedding), 0)
        from c b
        order by b.ord
      ) as sim_row
      from c a
    ) r;
  end if;

  maxsim := array_fill(0::double precision, array[greatest(n, 1)]);
  chosen := array_fill(false, array[greatest(n, 1)]);
  ex_used := array_fill(false, array[greatest(n_ex, 1)]);
  per_src := array_fill(0, array[n_src]);

  loop
    if pos % cfg.page_size = 0 then
      per_src := array_fill(0, array[n_src]);
    end if;
    pos := pos + 1;
    best := 0;
    pick_ex := 0;

    -- Explore slot: the best unused Popular item whose source has room on this page.
    if k is not null and pos % k = 0 then
      for j in 1 .. n_ex loop
        if not ex_used[j] and per_src[ex_src[j]] < cfg.max_per_source then
          pick_ex := j;
          exit;
        end if;
      end loop;
    end if;

    if pick_ex = 0 then
      -- MMR step; also folds the previous pick into maxsim.
      best_val := '-infinity';
      capped := 0;
      capped_val := '-infinity';
      for j in 1 .. n loop
        if not chosen[j] then
          if prev > 0 then
            maxsim[j] := greatest(maxsim[j], sims[prev][j]);
          end if;
          val := cfg.mmr_lambda * rel[j] - (1 - cfg.mmr_lambda) * maxsim[j];
          if per_src[src[j]] < cfg.max_per_source then
            if val > best_val then
              best := j;
              best_val := val;
            end if;
          elsif val > capped_val then
            capped := j;
            capped_val := val;
          end if;
        end if;
      end loop;
      -- Every remaining source is capped on this page: break the cap rather than end.
      if best = 0 then
        best := capped;
      end if;
    end if;

    if pick_ex = 0 and best = 0 then
      -- Personal list exhausted: append what is left of the explore list.
      for j in 1 .. n_ex loop
        if not ex_used[j] then
          pick_ex := j;
          exit;
        end if;
      end loop;
    end if;

    if pick_ex > 0 then
      ex_used[pick_ex] := true;
      per_src[ex_src[pick_ex]] := per_src[ex_src[pick_ex]] + 1;
      out_ids := out_ids || ex_ids[pick_ex];
    elsif best > 0 then
      chosen[best] := true;
      prev := best;
      per_src[src[best]] := per_src[src[best]] + 1;
      out_ids := out_ids || ids[best];
    else
      exit;
    end if;
  end loop;

  return out_ids;
end;
$$;

-- Cards for `p_ids` from position p_after + 1 on, after feed_filter, at most p_limit.
create function private.feed_page(p_user uuid, p_ids text[], p_after integer, p_limit integer)
returns table (pos integer, card public.feed_card)
language sql
stable
set search_path = ''
as $$
  with page as (
    select f.id, (p_after + f.ord)::integer as pos
    from private.feed_filter(p_user, p_ids[p_after + 1 :]) f
    order by f.ord
    limit p_limit
  )
  select page.pos, c
  from public.feed_cards((select array_agg(page.id order by page.pos) from page)) c
  join page on page.id = c.id
  order by page.pos
$$;

-- ---------------------------------------------------------------------------
-- My Feed (browser → Supabase RPC with the user's JWT).
--
--   First page:  my_feed()                     -- or my_feed(p_refresh => true)
--   Next pages:  my_feed(p_snapshot_id => <snapshot_id of the rows>, p_after => <last pos>)
--
-- Returns (snapshot_id, pos, card): pos is the 1-based position in the snapshot; card
-- is the W3 public.feed_card. The first page reuses the user's newest snapshot when it
-- is younger than snapshot_minutes, else ranks anew; later pages slice the given
-- snapshot, so pagination is stable and costs no re-ranking. If that snapshot is gone
-- (older than 2 h), a new one is computed and served from the top: a snapshot_id in the
-- result that differs from the one requested means "the list restarted".
-- Hidden items and blocked tags/sources are filtered when each page is sliced.
-- ---------------------------------------------------------------------------
create function public.my_feed(
  p_snapshot_id bigint default null,
  p_after integer default 0,
  p_limit integer default null,
  p_refresh boolean default false
)
returns table (snapshot_id bigint, pos integer, card public.feed_card)
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_user  uuid := auth.uid();
  cfg     public.ranking_config := private.ranking();
  v_id    bigint;
  v_ids   text[];
  v_after integer := greatest(coalesce(p_after, 0), 0);
  v_limit integer := least(greatest(coalesce(p_limit, cfg.page_size), 1), 60);
begin
  if v_user is null then
    raise exception 'sign in required' using errcode = '42501';
  end if;

  if not coalesce(p_refresh, false) then
    if p_snapshot_id is not null then
      select s.id, s.item_ids into v_id, v_ids
      from public.feed_snapshots s
      where s.id = p_snapshot_id and s.user_id = v_user;
    else
      v_after := 0;
      select s.id, s.item_ids into v_id, v_ids
      from public.feed_snapshots s
      where s.user_id = v_user and s.created_at > now() - make_interval(mins => cfg.snapshot_minutes)
      order by s.created_at desc
      limit 1;
    end if;
  end if;

  if v_id is null then
    v_after := 0;
    delete from public.feed_snapshots s
    where s.created_at < now() - interval '2 hours';
    insert into public.feed_snapshots (user_id, item_ids)
    values (v_user, private.rank_feed(v_user, now()))
    returning id, item_ids into v_id, v_ids;
  end if;

  return query
  select v_id, p.pos, p.card from private.feed_page(v_user, v_ids, v_after, v_limit) p;
end;
$$;

-- Telegram digest (§16 W4 step 6): the user's My Feed top n, ranked fresh, no snapshot.
-- service_role / postgres only.
create function public.feed_for_user(p_user_id uuid, p_n integer default 10)
returns setof public.feed_card
language sql
volatile
security definer
set search_path = ''
as $$
  select (p.card).*
  from private.feed_page(
    p_user_id, private.rank_feed(p_user_id, now()), 0, least(greatest(coalesce(p_n, 10), 1), 60)
  ) p
  order by p.pos
$$;

-- ---------------------------------------------------------------------------
-- User actions. All SECURITY DEFINER, user from auth.uid() only.
-- ---------------------------------------------------------------------------

-- Signed-in user, or 42501 (PostgREST answers 401/403).
create function private.require_user()
returns uuid
language plpgsql
stable
set search_path = ''
as $$
declare
  v_user uuid := auth.uid();
begin
  if v_user is null then
    raise exception 'sign in required' using errcode = '42501';
  end if;
  return v_user;
end;
$$;

-- Cheap flood guard for event-like writes: at most 600 rows per user per hour.
create function private.check_rate(p_user uuid, p_table text, p_new integer)
returns void
language plpgsql
stable
set search_path = ''
as $$
declare
  v_recent integer;
begin
  if p_table = 'events' then
    select count(*) into v_recent from (
      select 1 from public.events e
      where e.user_id = p_user and e.created_at > now() - interval '1 hour'
      limit 600
    ) x;
  else
    select count(*) into v_recent from (
      select 1 from public.impressions m
      where m.user_id = p_user and m.created_at > now() - interval '1 hour'
      limit 600
    ) x;
  end if;
  if v_recent + p_new > 600 then
    raise exception 'too many % in the last hour', p_table using errcode = '54000';
  end if;
end;
$$;

-- Reactions: upvote | downvote | bookmark | hide | less_like_this, on or off.
-- upvote and downvote exclude each other. hide and less_like_this drop the item (and its
-- near-duplicates) from the next page; less_like_this also weighs on learning (W5).
create function public.item_action(p_item_id text, p_kind text, p_on boolean default true)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_user uuid := private.require_user();
begin
  if p_kind is null or p_kind not in ('upvote', 'downvote', 'bookmark', 'hide', 'less_like_this') then
    raise exception 'unknown action %', p_kind using errcode = '22023';
  end if;
  if not exists (select 1 from public.items i where i.id = p_item_id) then
    raise exception 'unknown item' using errcode = '22023';
  end if;

  if coalesce(p_on, true) then
    perform private.check_rate(v_user, 'events', 1);
    if p_kind in ('upvote', 'downvote') then
      delete from public.events e
      where e.user_id = v_user and e.item_id = p_item_id
        and e.kind = case p_kind when 'upvote' then 'downvote' else 'upvote' end;
    end if;
    insert into public.events (user_id, item_id, kind)
    values (v_user, p_item_id, p_kind)
    on conflict (user_id, item_id, kind)
      where kind in ('upvote', 'downvote', 'bookmark', 'hide', 'less_like_this')
      do nothing;
  else
    delete from public.events e
    where e.user_id = v_user and e.item_id = p_item_id and e.kind = p_kind;
  end if;
end;
$$;

-- Batched click / dwell events (the web sends them on pagehide):
--   [{"item_id": "...", "kind": "click"}, {"item_id": "...", "kind": "dwell", "dwell_ms": 42000}]
-- At most 50 per call. Unknown items and malformed entries are skipped; dwell is capped
-- at 1 h. Returns the number stored.
create function public.record_events(p_events jsonb)
returns integer
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_user  uuid := private.require_user();
  v_count integer;
begin
  if jsonb_typeof(p_events) is distinct from 'array' then
    raise exception 'p_events must be a JSON array' using errcode = '22023';
  end if;
  if jsonb_array_length(p_events) > 50 then
    raise exception 'at most 50 events per call' using errcode = '22023';
  end if;
  perform private.check_rate(v_user, 'events', jsonb_array_length(p_events));

  insert into public.events (user_id, item_id, kind, dwell_ms)
  select v_user, x.item_id, x.kind, case when x.kind = 'dwell' then least(x.dwell_ms, 3600000) end
  from jsonb_to_recordset(
    (select jsonb_agg(e) from jsonb_array_elements(p_events) e where jsonb_typeof(e) = 'object')
  ) as x (item_id text, kind text, dwell_ms integer)
  join public.items i on i.id = x.item_id
  where x.kind = 'click' or (x.kind = 'dwell' and x.dwell_ms >= 0);
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

-- One row per rendered My Feed page.
create function public.record_impression(p_snapshot_id bigint, p_item_ids text[])
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_user uuid := private.require_user();
begin
  if coalesce(cardinality(p_item_ids), 0) not between 1 and 60 then
    raise exception 'between 1 and 60 item ids' using errcode = '22023';
  end if;
  if p_snapshot_id is not null and not exists (
    select 1 from public.feed_snapshots s where s.id = p_snapshot_id and s.user_id = v_user
  ) then
    raise exception 'unknown snapshot' using errcode = '22023';
  end if;
  perform private.check_rate(v_user, 'impressions', 1);
  insert into public.impressions (user_id, snapshot_id, item_ids)
  values (v_user, p_snapshot_id, p_item_ids);
end;
$$;

-- Onboarding (§15 Cold start): replace the followed tags (at least 3 active tags) and
-- optionally set the free-text profile (null keeps it, '' clears it). The text is
-- embedded by the next ingest run (profile_embedded_at stays null until then). Blocked
-- tags among p_tags become followed. Drops cached snapshots, so My Feed re-ranks.
create function public.set_followed_tags(p_tags text[], p_profile_text text default null)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_user uuid := private.require_user();
  v_tags text[];
begin
  select coalesce(array_agg(distinct t.slug), '{}') into v_tags
  from public.tags t
  where t.active and t.slug = any(p_tags);
  if cardinality(v_tags) < 3 then
    raise exception 'pick at least 3 tags' using errcode = '22023';
  end if;
  if cardinality(v_tags) < (select count(distinct x) from unnest(p_tags) x) then
    raise exception 'unknown tag' using errcode = '22023';
  end if;

  delete from public.user_tags ut
  where ut.user_id = v_user and ut.state = 'follow' and ut.tag <> all(v_tags);
  insert into public.user_tags (user_id, tag, state)
  select v_user, t, 'follow' from unnest(v_tags) t
  on conflict (user_id, tag) do update set state = 'follow';

  insert into public.profiles (user_id) values (v_user) on conflict do nothing;
  if p_profile_text is not null then
    update public.profiles p set profile_text = nullif(btrim(p_profile_text), '')
    where p.user_id = v_user;
  end if;

  delete from public.feed_snapshots s where s.user_id = v_user;
end;
$$;

-- Follow / block / clear one tag (p_state null clears). Blocking applies to the next
-- page; following changes the ranking, so the web should refresh My Feed after it.
create function public.set_tag_state(p_tag text, p_state text)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_user uuid := private.require_user();
begin
  if p_state is null then
    delete from public.user_tags ut where ut.user_id = v_user and ut.tag = p_tag;
    return;
  end if;
  if p_state not in ('follow', 'block') then
    raise exception 'state must be follow, block or null' using errcode = '22023';
  end if;
  if not exists (select 1 from public.tags t where t.slug = p_tag and t.active) then
    raise exception 'unknown tag' using errcode = '22023';
  end if;
  insert into public.user_tags (user_id, tag, state) values (v_user, p_tag, p_state)
  on conflict (user_id, tag) do update set state = excluded.state;
end;
$$;

-- Block or unblock a source; applies to the next page.
create function public.set_source_blocked(p_source_id bigint, p_blocked boolean default true)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_user uuid := private.require_user();
begin
  if not coalesce(p_blocked, true) then
    delete from public.user_sources us where us.user_id = v_user and us.source_id = p_source_id;
    return;
  end if;
  if not exists (select 1 from public.sources s where s.id = p_source_id) then
    raise exception 'unknown source' using errcode = '22023';
  end if;
  insert into public.user_sources (user_id, source_id) values (v_user, p_source_id)
  on conflict do nothing;
end;
$$;

-- Display name, language and free-text profile; null keeps a field, '' clears the text.
create function public.update_profile(
  p_display_name text default null,
  p_lang text default null,
  p_profile_text text default null
)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_user uuid := private.require_user();
begin
  insert into public.profiles (user_id) values (v_user) on conflict do nothing;
  update public.profiles p set
    display_name = coalesce(nullif(btrim(p_display_name), ''), p.display_name),
    lang = coalesce(p_lang, p.lang),
    profile_text = case when p_profile_text is null then p.profile_text
                        else nullif(btrim(p_profile_text), '') end
  where p.user_id = v_user;
end;
$$;

-- Bookmarks, newest first. SECURITY INVOKER: RLS on events limits it to the caller.
create function public.my_bookmarks(p_limit integer default 24, p_offset integer default 0)
returns setof public.feed_card
language sql
stable
security invoker
set search_path = ''
as $$
  with page as (
    select e.item_id, row_number() over (order by e.created_at desc, e.id desc) as rn
    from public.events e
    where e.user_id = (select auth.uid()) and e.kind = 'bookmark'
    order by rn
    limit least(greatest(coalesce(p_limit, 24), 1), 60)
    offset greatest(coalesce(p_offset, 0), 0)
  )
  select c.* from public.feed_cards((select array_agg(page.item_id order by page.rn) from page)) c
$$;

-- Reading history: clicked items, most recently clicked first.
create function public.my_history(p_limit integer default 24, p_offset integer default 0)
returns setof public.feed_card
language sql
stable
security invoker
set search_path = ''
as $$
  with clicks as (
    select e.item_id, max(e.created_at) as last_at
    from public.events e
    where e.user_id = (select auth.uid()) and e.kind = 'click'
    group by e.item_id
  ),
  page as (
    select c.item_id, row_number() over (order by c.last_at desc, c.item_id) as rn
    from clicks c
    order by rn
    limit least(greatest(coalesce(p_limit, 24), 1), 60)
    offset greatest(coalesce(p_offset, 0), 0)
  )
  select c.* from public.feed_cards((select array_agg(page.item_id order by page.rn) from page)) c
$$;

-- "Delete my history": clicks, dwell, impressions and feed snapshots. Explicit choices
-- (votes, bookmarks, hides, follows, blocks) stay. Returns the number of events removed.
create function public.delete_my_history()
returns integer
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_user  uuid := private.require_user();
  v_count integer;
begin
  delete from public.events e where e.user_id = v_user and e.kind in ('click', 'dwell');
  get diagnostics v_count = row_count;
  delete from public.impressions m where m.user_id = v_user;
  delete from public.feed_snapshots s where s.user_id = v_user;
  return v_count;
end;
$$;

-- ---------------------------------------------------------------------------
-- Function grants. Default privileges revoke EXECUTE from public/anon/authenticated,
-- but Supabase grants service_role EXECUTE on new public functions; revoke everything
-- first so each grant below is the whole story.
-- ---------------------------------------------------------------------------
revoke all on function
  private.ranking(),
  private.profiles_touch(),
  private.handle_new_user(),
  private.feed_filter(uuid, text[]),
  private.rank_feed(uuid, timestamptz),
  private.feed_page(uuid, text[], integer, integer),
  private.require_user(),
  private.check_rate(uuid, text, integer)
from public, anon, authenticated, service_role;

revoke all on function
  public.popular_score(public.ranking_config, real, bigint, real, timestamptz, timestamptz),
  public.popular_pool(timestamptz, integer),
  public.follow_centroid(uuid, timestamptz),
  public.my_feed(bigint, integer, integer, boolean),
  public.feed_for_user(uuid, integer),
  public.item_action(text, text, boolean),
  public.record_events(jsonb),
  public.record_impression(bigint, text[]),
  public.set_followed_tags(text[], text),
  public.set_tag_state(text, text),
  public.set_source_blocked(bigint, boolean),
  public.update_profile(text, text, text),
  public.my_bookmarks(integer, integer),
  public.my_history(integer, integer),
  public.delete_my_history()
from public, anon, authenticated, service_role;

-- popular_feed (anon) reads the weights through this.
grant execute on function private.ranking() to anon, authenticated, service_role;

-- Public read side (same audience as W3's popular_feed).
grant execute on function
  public.popular_score(public.ranking_config, real, bigint, real, timestamptz, timestamptz),
  public.popular_pool(timestamptz, integer)
to anon, authenticated, service_role;

-- Signed-in users.
grant execute on function
  public.my_feed(bigint, integer, integer, boolean),
  public.item_action(text, text, boolean),
  public.record_events(jsonb),
  public.record_impression(bigint, text[]),
  public.set_followed_tags(text[], text),
  public.set_tag_state(text, text),
  public.set_source_blocked(bigint, boolean),
  public.update_profile(text, text, text),
  public.my_bookmarks(integer, integer),
  public.my_history(integer, integer),
  public.delete_my_history()
to authenticated;

-- Pipeline only (Telegram digest, profile embedding). postgres owns them anyway.
grant execute on function
  public.feed_for_user(uuid, integer),
  public.follow_centroid(uuid, timestamptz)
to service_role;
