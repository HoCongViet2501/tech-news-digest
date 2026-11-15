-- W3 public read side (docs/PLAN.md §16 W3, §15 Per-user score, §14 search).
--
-- Read-only RPC functions for the public web app, called with the anon key.
--   * All are SECURITY INVOKER with an empty search_path: they run as the caller
--     (anon / authenticated), so table grants and RLS still apply, and they only
--     touch columns those roles can already SELECT.
--   * Default privileges revoke EXECUTE everywhere (content_store migration), so
--     every function below is granted explicitly.
--   * Retired tags (tags.active = false, 20261115095000_tags_active) are hidden.
--   * Card-shaped results share one composite type, public.feed_card, built by
--     public.feed_cards() so the feed, search and item page stay consistent.

-- ---------------------------------------------------------------------------
-- Search: typo tolerance on titles. The W1 trigram index is on the raw title,
-- but search compares unaccented lowercase text ("kien truc" ~ "Kiến trúc"), so
-- the index has to be on that same expression.
-- ---------------------------------------------------------------------------
drop index if exists public.items_title_trgm_idx;
create index items_title_unaccent_trgm_idx on public.items
  using gin (public.immutable_unaccent(lower(title)) extensions.gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- Helpers.
-- ---------------------------------------------------------------------------

-- Host part of a URL, lowercased ("https://news.ycombinator.com/item?id=1" -> "news.ycombinator.com").
create function public.url_host(p_url text)
returns text
language sql
immutable
parallel safe
strict
set search_path = ''
as $$
  select lower(substring(p_url from '^[A-Za-z][A-Za-z0-9+.-]*://(?:[^/?#@]*@)?([^/?#:]+)'))
$$;

-- A "site" for "also on" and the multi-source bonus: dev.to tag feeds (or two HN
-- endpoints) are one site; each RSS feed is its own site.
create function public.source_site(p_kind text, p_source_id bigint)
returns text
language sql
immutable
parallel safe
set search_path = ''
as $$
  select case when p_kind = 'rss' then 'rss:' || p_source_id::text else p_kind end
$$;

-- Popular score: the user-independent part of the §15 per-user score.
--   popularity = pop_pct + bonus when the item (or its cluster) was seen on >= 2 sites
--   quality    = LLM quality 0..1, 0.5 when not scored yet
--   freshness  = exp(-age_hours / 36)
-- §15 weighs popularity 0.25, quality 0.20, freshness 0.15 (interest takes the other
-- 0.40). Without interest the three are renormalized to sum to 1. These constants are
-- the only place the Popular weights live; W4 moves them to ranking_config (§15).
create function public.popular_score(
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
set search_path = ''
as $$
  with w as (
    select
      0.25 / 0.60 as w_popularity,
      0.20 / 0.60 as w_quality,
      0.15 / 0.60 as w_freshness,
      0.20        as multi_source_bonus,
      36.0        as freshness_hours
  )
  select
      w.w_popularity * (coalesce(p_pop_pct, 0)
                        + case when coalesce(p_n_sites, 0) >= 2 then w.multi_source_bonus else 0 end)
    + w.w_quality * coalesce(p_quality, 0.5)
    + w.w_freshness * exp(
        -greatest(extract(epoch from (p_as_of - p_published_at)) / 3600.0, 0) / w.freshness_hours
      )
  from w
$$;

-- ---------------------------------------------------------------------------
-- Card rows.
-- ---------------------------------------------------------------------------
create type public.feed_card as (
  id             text,
  title          text,
  url            text,
  discussion_url text,
  description    text,     -- excerpt (300 chars) in lists, full stored excerpt on the item page
  image_url      text,
  lang           text,
  reading_min    smallint,
  published_at   timestamptz,
  tldr           text,     -- item page only
  source_id      bigint,
  source_name    text,
  source_kind    text,
  source_domain  text,     -- for the favicon: discussion host, else article host
  upvotes        bigint,   -- summed over sites (best mention per site) across the cluster
  comments       bigint,
  tags           jsonb,    -- [{slug, name}] by confidence
  mentions       jsonb,    -- [{source_id, source_name, source_kind, discussion_url, score, comments}]
  siblings       jsonb     -- other items of the near-duplicate cluster: [{id, title, url, source_name}]
);

-- Builds cards for the given ids, in the given order. Unknown ids are skipped.
create function public.feed_cards(p_ids text[], p_full boolean default false)
returns setof public.feed_card
language sql
stable
parallel safe
security invoker
set search_path = ''
as $$
  select
    i.id,
    i.title,
    i.url,
    i.discussion_url,
    case when p_full then i.description else left(i.description, 300) end,
    i.image_url,
    i.lang,
    i.reading_min,
    i.published_at,
    case when p_full then i.tldr end,
    s.id,
    s.name,
    s.kind,
    public.url_host(coalesce(i.discussion_url, i.url)),
    st.upvotes,
    st.comments,
    coalesce(tg.tags, '[]'::jsonb),
    coalesce(st.mentions, '[]'::jsonb),
    coalesce(sb.siblings, '[]'::jsonb)
  from unnest(p_ids) with ordinality as ids (id, ord)
  join public.items i on i.id = ids.id
  join public.sources s on s.id = i.source_id
  cross join lateral (
    select
      coalesce(sum(m.native_score), 0)::bigint as upvotes,
      coalesce(sum(m.native_comments), 0)::bigint as comments,
      jsonb_agg(
        jsonb_build_object(
          'source_id', m.source_id,
          'source_name', ms.name,
          'source_kind', ms.kind,
          'discussion_url', m.discussion_url,
          'score', m.native_score,
          'comments', m.native_comments
        )
        order by m.native_score desc, m.source_id
      ) as mentions
    from (
      -- One mention per site across the cluster: the own source, else the best-scored.
      select distinct on (public.source_site(gs.kind, gs.id)) gm.*
      from (
        select im.source_id, im.discussion_url, im.native_score, im.native_comments
        from public.items g
        join public.item_mentions im on im.item_id = g.id
        where g.id = i.id or g.cluster_id = i.cluster_id
        union all
        -- Items without mention rows yet count as seen by their own source.
        select g.source_id, g.discussion_url, g.native_score, g.native_comments
        from public.items g
        where (g.id = i.id or g.cluster_id = i.cluster_id)
          and not exists (select 1 from public.item_mentions im where im.item_id = g.id)
      ) gm
      join public.sources gs on gs.id = gm.source_id
      order by public.source_site(gs.kind, gs.id), (gm.source_id = i.source_id) desc, gm.native_score desc
    ) m
    join public.sources ms on ms.id = m.source_id
  ) st
  left join lateral (
    select jsonb_agg(jsonb_build_object('slug', t.slug, 'name', t.name) order by it.confidence desc, t.slug) as tags
    from public.item_tags it
    join public.tags t on t.slug = it.tag and t.active
    where it.item_id = i.id
  ) tg on true
  left join lateral (
    select jsonb_agg(
             jsonb_build_object('id', g.id, 'title', g.title, 'url', g.url, 'source_name', gs.name)
             order by g.published_at, g.id
           ) as siblings
    from public.items g
    join public.sources gs on gs.id = g.source_id
    where g.cluster_id = i.cluster_id and g.id <> i.id
  ) sb on true
  order by ids.ord
$$;

-- ---------------------------------------------------------------------------
-- Popular feed: one card per cluster (its canonical item), last 7 days.
--   p_sort   'popular' (popular_score) | 'upvoted' (upvotes) | 'discussed' (comments)
--   p_tag    optional tag slug; matches the tag and all its descendants (a retired
--            or unknown tag matches nothing)
--   p_as_of  ranking time; pass the value of the first page to every later page so
--            freshness and the item pool (first_seen_at <= as_of) stay fixed and
--            offset pagination does not skip or repeat items
-- ---------------------------------------------------------------------------
create function public.popular_feed(
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
    select coalesce(p_as_of, now()) as as_of
  ),
  tagset (slug) as (
    select t.slug from public.tags t where t.slug = p_tag and t.active
    union
    select t.slug from public.tags t join tagset ts on t.parent = ts.slug where t.active
  ),
  pool as (
    select
      i.id,
      coalesce(c.canonical_item_id, i.id) as head,
      i.source_id,
      i.native_score,
      i.native_comments,
      i.pop_pct,
      i.quality,
      i.published_at
    from public.items i
    left join public.clusters c on c.id = i.cluster_id
    cross join params
    where i.published_at > params.as_of - interval '7 days'
      and i.first_seen_at <= params.as_of
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
  ),
  ranked as (
    select
      h.id,
      h.published_at,
      case p_sort
        when 'upvoted' then st.upvotes
        when 'discussed' then st.comments
        else public.popular_score(g.pop_pct, st.n_sites, g.quality, h.published_at, params.as_of)
      end as sort_key
    from grouped g
    join stats st on st.head = g.head
    join pool h on h.id = g.head
    cross join params
    where p_tag is null
       or exists (
            select 1 from public.item_tags it
            where it.item_id = h.id and it.tag in (select ts.slug from tagset ts)
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
-- Item page.
-- ---------------------------------------------------------------------------
create function public.item_detail(p_id text)
returns setof public.feed_card
language sql
stable
security invoker
set search_path = ''
as $$
  select c.* from public.feed_cards(array[p_id], true) c
$$;

-- ---------------------------------------------------------------------------
-- Tags with the number of items from the last 7 days, counting descendants too
-- (the same set a tag page shows).
-- ---------------------------------------------------------------------------
create function public.tag_list()
returns table (slug text, name text, parent text, description text, item_count bigint)
language sql
stable
security invoker
set search_path = ''
as $$
  with recursive
  tree (ancestor, slug) as (
    select t.slug, t.slug from public.tags t where t.active
    union
    select tree.ancestor, c.slug from tree join public.tags c on c.parent = tree.slug where c.active
  ),
  recent as (
    select it.item_id, it.tag
    from public.item_tags it
    join public.items i on i.id = it.item_id
    where i.published_at > now() - interval '7 days'
  ),
  counts as (
    select tree.ancestor as slug, count(distinct r.item_id) as n
    from tree
    join recent r on r.tag = tree.slug
    group by tree.ancestor
  )
  select t.slug, t.name, t.parent, t.description, coalesce(c.n, 0)::bigint
  from public.tags t
  left join counts c on c.slug = t.slug
  where t.active
  order by coalesce(c.n, 0) desc, t.name
$$;

-- ---------------------------------------------------------------------------
-- Search: full-text (simple + unaccent, the same normalization as items.fts) OR
-- trigram word similarity on the unaccented title for typos. Exact token matches
-- rank first, then title similarity, then recency.
-- ---------------------------------------------------------------------------
create function public.search_items(p_q text, p_limit integer default 24, p_offset integer default 0)
returns setof public.feed_card
language sql
stable
security invoker
set search_path = ''
as $$
  with q as (
    select norm, websearch_to_tsquery('simple'::regconfig, norm) as tsq
    from (select public.immutable_unaccent(lower(btrim(coalesce(p_q, '')))) as norm) n
    where length(norm) >= 2
  ),
  hits as (
    select
      i.id,
      i.published_at,
        (case when i.fts @@ q.tsq then 1.0 else 0.0 end)
      + extensions.word_similarity(q.norm, public.immutable_unaccent(lower(i.title)))
      + 0.3 * exp(-greatest(extract(epoch from (now() - i.published_at)) / 3600.0, 0) / (24 * 14))
        as rank
    from public.items i
    cross join q
    where i.fts @@ q.tsq
       or q.norm operator(extensions.<%) public.immutable_unaccent(lower(i.title))
  ),
  page as (
    select h.id, row_number() over (order by h.rank desc, h.published_at desc, h.id) as rn
    from hits h
    order by rn
    limit least(greatest(coalesce(p_limit, 24), 1), 60)
    offset greatest(coalesce(p_offset, 0), 0)
  )
  select c.*
  from public.feed_cards((select array_agg(page.id order by page.rn) from page)) c
$$;

-- ---------------------------------------------------------------------------
-- Grants. Read-only content, so anon and authenticated both get EXECUTE.
-- ---------------------------------------------------------------------------
revoke all on function
  public.url_host(text),
  public.source_site(text, bigint),
  public.popular_score(real, bigint, real, timestamptz, timestamptz),
  public.feed_cards(text[], boolean),
  public.popular_feed(text, text, timestamptz, integer, integer),
  public.item_detail(text),
  public.tag_list(),
  public.search_items(text, integer, integer)
from public;

grant execute on function
  public.url_host(text),
  public.source_site(text, bigint),
  public.popular_score(real, bigint, real, timestamptz, timestamptz),
  public.feed_cards(text[], boolean),
  public.popular_feed(text, text, timestamptz, integer, integer),
  public.item_detail(text),
  public.tag_list(),
  public.search_items(text, integer, integer)
to anon, authenticated;

grant usage on type public.feed_card to anon, authenticated;
