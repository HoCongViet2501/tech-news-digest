import { PAGE_SIZE, toCard, type Card, type FeedQuery, type Sort } from "./card";
import type { Client } from "./supabase";

// Read-side RPCs (supabase/migrations/*_w3_public_read.sql). Shared by the cached server
// loaders (first page) and the browser (later pages of the infinite scroll).

export const SEARCH_MAX_LENGTH = 100;

export async function fetchFeed(
  client: Client,
  sort: Sort,
  tag: string | null,
  asOf: string,
  offset = 0,
): Promise<Card[]> {
  const { data, error } = await client.rpc("popular_feed", {
    p_sort: sort,
    p_tag: tag ?? undefined,
    p_as_of: asOf,
    p_limit: PAGE_SIZE,
    p_offset: offset,
  });
  if (error) throw new Error(`popular_feed failed: ${error.message}`);
  return data.map(toCard);
}

export async function fetchSearch(client: Client, q: string, offset = 0): Promise<Card[]> {
  const { data, error } = await client.rpc("search_items", {
    p_q: q.slice(0, SEARCH_MAX_LENGTH),
    p_limit: PAGE_SIZE,
    p_offset: offset,
  });
  if (error) throw new Error(`search_items failed: ${error.message}`);
  return data.map(toCard);
}

export function fetchPage(client: Client, query: FeedQuery, offset: number): Promise<Card[]> {
  return query.kind === "feed"
    ? fetchFeed(client, query.sort, query.tag, query.asOf, offset)
    : fetchSearch(client, query.q, offset);
}
