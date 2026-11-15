import { createHash } from "node:crypto";

import pg from "pg";

// Test-only content for the e2e suite. Everything here is marked as a fixture:
// sources have kind `e2e_*` and URLs on the reserved `.invalid` TLD, tags start with
// `e2e-`. Seeding is an idempotent upsert (never a truncate), and cleanup deletes only
// those rows, because the local database is shared with other work.

export const DB_URL = process.env.E2E_DB_URL ?? "postgresql://postgres:postgres@127.0.0.1:54322/postgres";

const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

export const itemId = (key: string) => createHash("sha1").update(`e2e:${key}`).digest("hex");

export const SOURCES = {
  hn: { kind: "e2e_hn", name: "E2E Hacker News", url: "https://e2e.invalid/hn" },
  lobsters: { kind: "e2e_lobsters", name: "E2E Lobsters", url: "https://e2e.invalid/lobsters" },
} as const;

export const TAGS = {
  topic: { slug: "e2e-topic", name: "E2E Topic", parent: null, description: "Fixture parent tag." },
  child: { slug: "e2e-child", name: "E2E Child", parent: "e2e-topic", description: "Fixture child tag." },
} as const;

type Source = keyof typeof SOURCES;

type Fixture = {
  key: string;
  source: Source;
  title: string;
  description: string | null;
  lang: string | null;
  imageUrl: string | null;
  hoursAgo: number;
  popPct: number;
  quality: number;
  score: number;
  comments: number;
  tags: string[];
  /** Extra "also on" mentions. */
  alsoOn?: { source: Source; score: number; comments: number }[];
};

export const ITEMS = {
  vietnamese: {
    key: "vietnamese",
    source: "hn",
    title: "Kiến trúc microservices cho người mới bắt đầu",
    description: "So sánh monolith & microservices: khi nào <nên> tách dịch vụ?",
    lang: "vi",
    imageUrl: null,
    hoursAgo: 1,
    popPct: 1,
    quality: 1,
    score: 320,
    comments: 140,
    tags: [TAGS.child.slug],
    alsoOn: [{ source: "lobsters", score: 45, comments: 12 }],
  },
  special: {
    key: "special",
    source: "lobsters",
    title: "Rust & WebAssembly: <fast> builds 🚀",
    description: "Ship it & smile <3 🎉",
    lang: "en",
    // Unreachable on purpose: the card must fall back to the favicon on a gradient.
    imageUrl: "https://e2e.invalid/missing-cover.png",
    hoursAgo: 2,
    popPct: 0.95,
    quality: 0.9,
    score: 150,
    comments: 30,
    tags: [TAGS.topic.slug],
  },
  untagged: {
    key: "untagged",
    source: "hn",
    title: "Postgres vacuum tuning notes (e2e untagged)",
    description: null,
    lang: "en",
    imageUrl: null,
    hoursAgo: 3,
    popPct: 0.9,
    quality: 0.8,
    score: 90,
    comments: 20,
    tags: [],
  },
  sibling: {
    key: "sibling",
    source: "lobsters",
    title: "Microservices architecture for beginners (e2e mirror)",
    description: null,
    lang: "en",
    imageUrl: null,
    hoursAgo: 1,
    popPct: 0.5,
    quality: 0.5,
    score: 10,
    comments: 2,
    tags: [],
  },
} satisfies Record<string, Fixture>;

/** Enough low-ranked filler for a second page, so infinite scroll has something to load. */
const FILLER: Fixture[] = Array.from({ length: 30 }, (_, n) => ({
  key: `filler-${n}`,
  source: "hn",
  title: `E2E filler story #${n + 1}`,
  description: null,
  lang: "en",
  imageUrl: null,
  hoursAgo: 4 + n,
  popPct: 0.05,
  quality: 0.1,
  score: 1,
  comments: 0,
  tags: [],
}));

const ALL: Fixture[] = [...Object.values(ITEMS), ...FILLER];

async function connect(): Promise<pg.Client> {
  const host = new URL(DB_URL).hostname;
  if (!LOCAL_HOSTS.has(host)) throw new Error(`E2E_DB_URL must point at a local database, got host ${host}`);
  const client = new pg.Client({ connectionString: DB_URL });
  await client.connect();
  return client;
}

export async function seed(): Promise<void> {
  const db = await connect();
  try {
    await db.query("begin");
    const sourceIds = {} as Record<Source, number>;
    for (const [key, s] of Object.entries(SOURCES) as [Source, (typeof SOURCES)[Source]][]) {
      const { rows } = await db.query<{ id: number }>(
        `insert into public.sources (kind, name, url, enabled, base_weight, cadence_hours)
         values ($1, $2, $3, false, 50, 24)
         on conflict (kind, url) do update set name = excluded.name
         returning id`,
        [s.kind, s.name, s.url],
      );
      sourceIds[key] = Number(rows[0].id);
    }

    for (const t of [TAGS.topic, TAGS.child]) {
      await db.query(
        `insert into public.tags (slug, name, parent, description) values ($1, $2, $3, $4)
         on conflict (slug) do update
           set name = excluded.name, parent = excluded.parent, description = excluded.description`,
        [t.slug, t.name, t.parent, t.description],
      );
    }
    // W2 retires tags missing from taxonomy.yaml (tags.active); keep the fixtures active.
    const { rowCount: hasActive } = await db.query(
      `select 1 from information_schema.columns
       where table_schema = 'public' and table_name = 'tags' and column_name = 'active'`,
    );
    if (hasActive) await db.query(`update public.tags set active = true where slug like 'e2e-%'`);

    for (const f of ALL) {
      const id = itemId(f.key);
      const discussion = `${SOURCES[f.source].url}/item/${f.key}`;
      await db.query(
        `insert into public.items (id, source_id, title, url, discussion_url, description, image_url, lang,
                                   reading_min, published_at, first_seen_at, native_score, native_comments,
                                   pop_pct, quality)
         values ($1, $2, $3, $4, $5, $6, $7, $8, 4,
                 now() - make_interval(hours => $9), now() - make_interval(hours => $9), $10, $11, $12, $13)
         on conflict (id) do update set
           source_id = excluded.source_id, title = excluded.title, url = excluded.url,
           discussion_url = excluded.discussion_url, description = excluded.description,
           image_url = excluded.image_url, lang = excluded.lang, reading_min = excluded.reading_min,
           published_at = excluded.published_at, first_seen_at = excluded.first_seen_at,
           native_score = excluded.native_score, native_comments = excluded.native_comments,
           pop_pct = excluded.pop_pct, quality = excluded.quality`,
        [
          id,
          sourceIds[f.source],
          f.title,
          `https://e2e.invalid/article/${f.key}`,
          discussion,
          f.description,
          f.imageUrl,
          f.lang,
          f.hoursAgo,
          f.score,
          f.comments,
          f.popPct,
          f.quality,
        ],
      );
      const mentions = [{ source: f.source, score: f.score, comments: f.comments }, ...(f.alsoOn ?? [])];
      for (const m of mentions) {
        await db.query(
          `insert into public.item_mentions (item_id, source_id, discussion_url, native_score, native_comments)
           values ($1, $2, $3, $4, $5)
           on conflict (item_id, source_id) do update set
             discussion_url = excluded.discussion_url, native_score = excluded.native_score,
             native_comments = excluded.native_comments`,
          [id, sourceIds[m.source], `${SOURCES[m.source].url}/item/${f.key}`, m.score, m.comments],
        );
      }
      for (const tag of f.tags) {
        await db.query(
          `insert into public.item_tags (item_id, tag, confidence, method) values ($1, $2, 0.95, 'rule')
           on conflict (item_id, tag) do update set confidence = excluded.confidence`,
          [id, tag],
        );
      }
    }

    // The mirror is a near-duplicate of the Vietnamese story: one cluster, one card.
    const head = itemId(ITEMS.vietnamese.key);
    const members = [head, itemId(ITEMS.sibling.key)];
    const { rows: cluster } = await db.query<{ id: number }>(
      `select id from public.clusters where canonical_item_id = $1`,
      [head],
    );
    const clusterId =
      cluster[0]?.id ??
      (
        await db.query<{ id: number }>(
          `insert into public.clusters (canonical_item_id, item_ids) values ($1, $2) returning id`,
          [head, members],
        )
      ).rows[0].id;
    await db.query(`update public.items set cluster_id = $1 where id = any($2)`, [clusterId, members]);

    await db.query("commit");
  } catch (err) {
    await db.query("rollback");
    throw err;
  } finally {
    await db.end();
  }
}

export async function cleanup(): Promise<void> {
  const db = await connect();
  try {
    await db.query("begin");
    const e2eSources = `select id from public.sources where kind like 'e2e\\_%'`;
    // Cascades to item_mentions, item_tags and clusters of these items.
    await db.query(`delete from public.items where source_id in (${e2eSources})`);
    await db.query(`delete from public.item_mentions where source_id in (${e2eSources})`);
    await db.query(`delete from public.tags where slug like 'e2e-%'`);
    await db.query(`delete from public.sources where kind like 'e2e\\_%'`);
    await db.query("commit");
  } catch (err) {
    await db.query("rollback");
    throw err;
  } finally {
    await db.end();
  }
}
