-- W2 (docs/PLAN.md §15 Tagging): tags are synced from taxonomy.yaml on every ingest run.
-- A tag removed from the YAML is retired (active = false), never deleted: item_tags
-- (and W4 user_tags) cascade on delete, so deleting would silently drop history and
-- users' follows. A retired tag is no longer assigned and the web hides it; putting it
-- back in the YAML re-activates it with its history intact. Same idea as sources.enabled.

alter table public.tags
  add column active boolean not null default true;
