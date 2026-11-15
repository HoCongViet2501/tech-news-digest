-- W2 (docs/PLAN.md §15 Dedupe): keep what W1 dropped on the floor.
--   * items.native_tags: the source's own tags (dev.to, Lobsters, Lemmy, ...). The tag
--     stage may run in a later ingest run than the fetch, so they must be stored.
--   * item_mentions: one row per (item, source). The same canonical URL seen on HN and
--     Lobsters is one item with two mentions; the card shows "also on", and the
--     cluster popularity bonus counts distinct sources. items.source_id stays the
--     first source that saw the item.

alter table public.items
  add column native_tags text[] not null default '{}';

create table public.item_mentions (
  item_id         text not null references public.items (id) on delete cascade,
  source_id       bigint not null references public.sources (id) on delete restrict,
  discussion_url  text,
  native_score    integer not null default 0,
  native_comments integer not null default 0,
  first_seen_at   timestamptz not null default now(),
  primary key (item_id, source_id)
);

create index item_mentions_source_idx on public.item_mentions (source_id, first_seen_at desc);

-- Backfill: every existing item was seen by its own source.
insert into public.item_mentions (item_id, source_id, discussion_url, native_score, native_comments, first_seen_at)
select id, source_id, discussion_url, native_score, native_comments, first_seen_at
from public.items
on conflict do nothing;

alter table public.item_mentions enable row level security;
revoke all on table public.item_mentions from anon, authenticated;
grant select on table public.item_mentions to anon, authenticated;
create policy "Public content is readable" on public.item_mentions
  for select to anon, authenticated using (true);
