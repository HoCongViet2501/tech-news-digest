import type { Database, Json } from "./database.types";

/** Row shape returned by popular_feed / search_items / item_detail (SQL type public.feed_card). */
export type FeedCardRow = Database["public"]["CompositeTypes"]["feed_card"];

export type Sort = "popular" | "upvoted" | "discussed";

export const SORTS: readonly { sort: Sort; label: string; href: string }[] = [
  { sort: "popular", label: "Popular", href: "/" },
  { sort: "upvoted", label: "Most upvoted", href: "/upvoted" },
  { sort: "discussed", label: "Discussed", href: "/discussed" },
];

/** One page of cards; matches the SQL default and divides evenly into 1–4 grid columns. */
export const PAGE_SIZE = 24;

export type TagRef = { slug: string; name: string };

export type Mention = {
  sourceId: number;
  sourceName: string;
  sourceKind: string;
  discussionUrl: string | null;
  score: number;
  comments: number;
};

export type Sibling = { id: string; title: string; url: string; sourceName: string };

export type Card = {
  id: string;
  title: string;
  url: string;
  discussionUrl: string | null;
  description: string | null;
  imageUrl: string | null;
  lang: string | null;
  readingMin: number | null;
  publishedAt: string;
  tldr: string | null;
  source: { id: number; name: string; kind: string; domain: string | null };
  upvotes: number;
  comments: number;
  tags: TagRef[];
  mentions: Mention[];
  siblings: Sibling[];
};

/** What the browser needs to fetch the pages after the first one. */
export type FeedQuery =
  | { kind: "feed"; sort: Sort; tag: string | null; asOf: string }
  | { kind: "search"; q: string };

function obj(value: Json): Record<string, Json | undefined> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function arr(value: Json | null): Record<string, Json | undefined>[] {
  return Array.isArray(value) ? value.map((v) => obj(v)) : [];
}

const str = (v: Json | undefined): string => (typeof v === "string" ? v : "");
const optStr = (v: Json | undefined): string | null => (typeof v === "string" && v ? v : null);
const num = (v: Json | undefined): number => (typeof v === "number" ? v : 0);

/** Normalizes a feed_card row: parses the jsonb columns and drops nulls the UI can't use. */
export function toCard(row: FeedCardRow): Card {
  return {
    id: row.id ?? "",
    title: row.title ?? "",
    url: row.url ?? "",
    discussionUrl: row.discussion_url,
    description: row.description,
    imageUrl: row.image_url,
    lang: row.lang,
    readingMin: row.reading_min,
    publishedAt: row.published_at ?? new Date(0).toISOString(),
    tldr: row.tldr,
    source: {
      id: row.source_id ?? 0,
      name: row.source_name ?? "",
      kind: row.source_kind ?? "",
      domain: row.source_domain,
    },
    upvotes: row.upvotes ?? 0,
    comments: row.comments ?? 0,
    tags: arr(row.tags)
      .map((t) => ({ slug: str(t.slug), name: str(t.name) }))
      .filter((t) => t.slug),
    mentions: arr(row.mentions).map((m) => ({
      sourceId: num(m.source_id),
      sourceName: str(m.source_name),
      sourceKind: str(m.source_kind),
      discussionUrl: optStr(m.discussion_url),
      score: num(m.score),
      comments: num(m.comments),
    })),
    siblings: arr(row.siblings).map((s) => ({
      id: str(s.id),
      title: str(s.title),
      url: str(s.url),
      sourceName: str(s.source_name),
    })),
  };
}
