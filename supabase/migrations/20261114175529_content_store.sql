-- W1 content store (docs/PLAN.md §14): sources, items, clusters, tags, item_tags.
-- User tables, ranking functions and their RLS come in W4.
--
-- Access model:
--   * The pipeline writes as `postgres` through the DB URL (bypasses RLS as table owner).
--   * The web reads public content with the anon key (W3): `anon` and `authenticated`
--     get SELECT only, both through grants and through RLS policies. No write policies.

create extension if not exists vector with schema extensions;
create extension if not exists unaccent with schema extensions;
create extension if not exists pg_trgm with schema extensions;

-- unaccent() is STABLE (it depends on the search_path and the dictionary), so it
-- cannot be used in a generated column. This wrapper pins the dictionary and is
-- declared IMMUTABLE, the usual workaround. W3 search uses it on the query side too.
create function public.immutable_unaccent(input text)
returns text
language sql
immutable
parallel safe
strict
set search_path = ''
as $$
  select extensions.unaccent('extensions.unaccent'::regdictionary, input)
$$;

-- ---------------------------------------------------------------------------
-- sources: one row per feed / API endpoint, synced from sources.yaml.
-- Natural key: (kind, url). Sources removed from the YAML are disabled, never
-- deleted, so their items keep a valid source_id.
-- ---------------------------------------------------------------------------
create table public.sources (
  id              bigint generated always as identity primary key,
  kind            text not null check (kind ~ '^[a-z][a-z0-9_]*$'),
  name            text not null check (name <> ''),
  url             text not null check (url <> ''),
  category        text,
  enabled         boolean not null default true,
  base_weight     smallint not null default 50 check (base_weight between 0 and 100),
  cadence_hours   integer not null default 24 check (cadence_hours > 0),
  etag            text,
  last_modified   text,
  last_fetched_at timestamptz,
  last_ok_at      timestamptz,
  last_error      text,
  created_at      timestamptz not null default now(),
  constraint sources_kind_url_key unique (kind, url)
);

-- ---------------------------------------------------------------------------
-- clusters: near-duplicate groups (§15 Dedupe).
-- ---------------------------------------------------------------------------
create table public.clusters (
  id                bigint generated always as identity primary key,
  canonical_item_id text not null,
  item_ids          text[] not null default '{}',
  created_at        timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- items: id = sha1 of the canonical URL (digest.models.item_id).
-- Stage columns double as the work queue (§13 Ingestion): null = not done yet.
-- ---------------------------------------------------------------------------
create table public.items (
  id              text primary key check (id ~ '^[0-9a-f]{40}$'),
  cluster_id      bigint references public.clusters (id) on delete set null,
  source_id       bigint not null references public.sources (id) on delete restrict,
  title           text not null,
  url             text not null,
  discussion_url  text,
  description     text,
  image_url       text,
  lang            text,
  reading_min     smallint check (reading_min >= 0),
  published_at    timestamptz not null,
  fetched_at      timestamptz not null default now(),
  native_score    integer not null default 0,
  native_comments integer not null default 0,
  pop_pct         real check (pop_pct between 0 and 1),
  quality         real check (quality between 0 and 1),
  tldr            text,
  embedding       extensions.halfvec(384),
  fts             tsvector generated always as (
                    to_tsvector(
                      'simple'::regconfig,
                      public.immutable_unaccent(coalesce(title, '') || ' ' || coalesce(description, ''))
                    )
                  ) stored,
  first_seen_at   timestamptz not null default now(),
  enriched_at     timestamptz,
  tagged_at       timestamptz,
  embedded_at     timestamptz,
  summarized_at   timestamptz
);

alter table public.clusters
  add constraint clusters_canonical_item_id_fkey
  foreign key (canonical_item_id) references public.items (id) on delete cascade;

-- No ANN (HNSW) index on embedding: the 7-day pool is scanned exactly (§14).
create index items_published_at_idx on public.items (published_at desc);
create index items_source_published_idx on public.items (source_id, published_at desc);
create index items_cluster_id_idx on public.items (cluster_id) where cluster_id is not null;
create index items_fts_idx on public.items using gin (fts);
create index items_title_trgm_idx on public.items using gin (title extensions.gin_trgm_ops);

-- "Stage not done yet" queues: small partial indexes ordered by arrival.
create index items_pending_enrich_idx on public.items (first_seen_at) where enriched_at is null;
create index items_pending_tag_idx on public.items (first_seen_at) where tagged_at is null;
create index items_pending_embed_idx on public.items (first_seen_at) where embedded_at is null;
create index items_pending_summarize_idx on public.items (first_seen_at) where summarized_at is null;

create index clusters_canonical_item_id_idx on public.clusters (canonical_item_id);

-- ---------------------------------------------------------------------------
-- tags (seeded from taxonomy.yaml in W2) and item_tags.
-- ---------------------------------------------------------------------------
create table public.tags (
  slug        text primary key check (slug ~ '^[a-z0-9][a-z0-9-]*$'),
  name        text not null,
  parent      text references public.tags (slug) on update cascade on delete set null,
  description text,
  aliases     text[] not null default '{}'
);

create table public.item_tags (
  item_id    text not null references public.items (id) on delete cascade,
  tag        text not null references public.tags (slug) on update cascade on delete cascade,
  confidence real not null check (confidence between 0 and 1),
  method     text not null check (method in ('native', 'rule', 'embed', 'llm')),
  created_at timestamptz not null default now(),
  primary key (item_id, tag)
);

create index item_tags_tag_idx on public.item_tags (tag);
create index tags_parent_idx on public.tags (parent) where parent is not null;

-- ---------------------------------------------------------------------------
-- Security. Supabase's default privileges grant ALL on new public tables and
-- sequences to anon/authenticated; take that back and grant SELECT only.
-- ---------------------------------------------------------------------------
alter table public.sources enable row level security;
alter table public.items enable row level security;
alter table public.clusters enable row level security;
alter table public.tags enable row level security;
alter table public.item_tags enable row level security;

revoke all on table public.sources, public.items, public.clusters, public.tags, public.item_tags
  from anon, authenticated;
revoke all on sequence public.sources_id_seq, public.clusters_id_seq from anon, authenticated;

grant select on table public.items, public.clusters, public.tags, public.item_tags
  to anon, authenticated;
-- sources: public columns only; fetch state (etag, errors, timings) stays private.
grant select (id, kind, name, url, category, enabled, base_weight) on table public.sources
  to anon, authenticated;

create policy "Public content is readable" on public.sources
  for select to anon, authenticated using (true);
create policy "Public content is readable" on public.items
  for select to anon, authenticated using (true);
create policy "Public content is readable" on public.clusters
  for select to anon, authenticated using (true);
create policy "Public content is readable" on public.tags
  for select to anon, authenticated using (true);
create policy "Public content is readable" on public.item_tags
  for select to anon, authenticated using (true);

-- Future objects created by the migration role in public get no anon/authenticated
-- access by default; every later migration (W4 user tables, RPC functions) must
-- grant explicitly. Closes the "new table is auto-exposed" hole for good.
alter default privileges for role postgres in schema public
  revoke all on tables from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke execute on functions from anon, authenticated;
-- EXECUTE for PUBLIC is a global default, so it can only be revoked globally.
alter default privileges for role postgres
  revoke execute on functions from public;
