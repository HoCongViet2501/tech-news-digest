import { cacheLife, cacheTag } from "next/cache";

import { toCard, type Card, type Sort } from "./card";
import { fetchFeed, fetchSearch } from "./queries";
import { anonClient } from "./supabase";

// Server loaders for public pages. Each result is cached with the "feed" profile
// (next.config.ts: revalidated every 15 min), so a page view costs no DB call and,
// once prerendered, no function invocation.
//
// Without NEXT_PUBLIC_SUPABASE_* (e.g. the CI build job, which has no database) the
// loaders return empty data and the pages say so; with them, a DB error throws, so a
// broken build or revalidation never caches an empty page.

export type FeedResult = { configured: boolean; asOf: string; cards: Card[] };

export type TagInfo = {
  slug: string;
  name: string;
  parent: string | null;
  description: string | null;
  itemCount: number;
};

export async function getFeed(sort: Sort, tag: string | null = null): Promise<FeedResult> {
  "use cache";
  cacheLife("feed");
  cacheTag("content");
  const asOf = new Date().toISOString();
  const client = anonClient();
  if (!client) return { configured: false, asOf, cards: [] };
  return { configured: true, asOf, cards: await fetchFeed(client, sort, tag, asOf) };
}

export async function searchItems(q: string): Promise<FeedResult> {
  "use cache";
  cacheLife("feed");
  cacheTag("content");
  const asOf = new Date().toISOString();
  const client = anonClient();
  if (!client || !q) return { configured: client !== null, asOf, cards: [] };
  return { configured: true, asOf, cards: await fetchSearch(client, q) };
}

export async function getItem(
  id: string,
): Promise<{ configured: boolean; asOf: string; card: Card | null }> {
  "use cache";
  cacheLife("feed");
  cacheTag("content");
  const asOf = new Date().toISOString();
  const client = anonClient();
  if (!client || !/^[0-9a-f]{40}$/.test(id)) return { configured: client !== null, asOf, card: null };
  const { data, error } = await client.rpc("item_detail", { p_id: id });
  if (error) throw new Error(`item_detail failed: ${error.message}`);
  return { configured: true, asOf, card: data.length ? toCard(data[0]) : null };
}

export async function getTags(): Promise<{ configured: boolean; tags: TagInfo[] }> {
  "use cache";
  cacheLife("feed");
  cacheTag("content");
  const client = anonClient();
  if (!client) return { configured: false, tags: [] };
  const { data, error } = await client.rpc("tag_list");
  if (error) throw new Error(`tag_list failed: ${error.message}`);
  return {
    configured: true,
    tags: data.map((t) => ({
      slug: t.slug,
      name: t.name,
      parent: t.parent ?? null,
      description: t.description ?? null,
      itemCount: t.item_count,
    })),
  };
}
