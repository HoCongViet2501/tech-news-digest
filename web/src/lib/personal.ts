import { PAGE_SIZE, toCard, type Card } from "./card";
import type { Client } from "./supabase";

// Signed-in RPCs and reads (supabase/migrations/*_w4_accounts.sql). Every write is an RPC
// that takes the user from auth.uid(); reads of the user's own rows go through RLS.

export const REACTIONS = ["upvote", "downvote", "bookmark", "hide", "less_like_this"] as const;
export type Reaction = (typeof REACTIONS)[number];

export type FeedRow = { snapshotId: number; pos: number; card: Card };

function fail(name: string, error: { message: string }): never {
  throw new Error(`${name} failed: ${error.message}`);
}

/** One My Feed page. No cursor = first page; `refresh` re-ranks. */
export async function fetchMyFeed(
  client: Client,
  cursor: { snapshotId: number; after: number } | null,
  refresh = false,
): Promise<FeedRow[]> {
  const { data, error } = await client.rpc("my_feed", {
    ...(cursor ? { p_snapshot_id: cursor.snapshotId, p_after: cursor.after } : {}),
    p_limit: PAGE_SIZE,
    ...(refresh ? { p_refresh: true } : {}),
  });
  if (error) fail("my_feed", error);
  return data.map((r) => ({ snapshotId: r.snapshot_id, pos: r.pos, card: toCard(r.card) }));
}

export async function itemAction(client: Client, itemId: string, kind: Reaction, on: boolean): Promise<void> {
  const { error } = await client.rpc("item_action", { p_item_id: itemId, p_kind: kind, p_on: on });
  if (error) fail("item_action", error);
}

export async function recordImpression(client: Client, snapshotId: number | null, itemIds: string[]): Promise<void> {
  if (!itemIds.length) return;
  const { error } = await client.rpc("record_impression", {
    p_snapshot_id: snapshotId as number,
    p_item_ids: itemIds.slice(0, 60),
  });
  if (error) fail("record_impression", error);
}

/** The user's reactions to these items, as item id → kinds. */
export async function fetchReactions(client: Client, itemIds: string[]): Promise<Map<string, Set<Reaction>>> {
  const out = new Map<string, Set<Reaction>>();
  if (!itemIds.length) return out;
  const { data, error } = await client
    .from("events")
    .select("item_id, kind")
    .in("item_id", itemIds)
    .in("kind", [...REACTIONS]);
  if (error) fail("events", error);
  for (const row of data) {
    const kinds = out.get(row.item_id) ?? new Set<Reaction>();
    kinds.add(row.kind as Reaction);
    out.set(row.item_id, kinds);
  }
  return out;
}

export type TagState = "follow" | "block";

export async function fetchMyTags(client: Client): Promise<{ tag: string; state: TagState }[]> {
  const { data, error } = await client.from("user_tags").select("tag, state").order("tag");
  if (error) fail("user_tags", error);
  return data.map((r) => ({ tag: r.tag, state: r.state as TagState }));
}

export async function countFollowedTags(client: Client): Promise<number> {
  const { count, error } = await client
    .from("user_tags")
    .select("tag", { count: "exact", head: true })
    .eq("state", "follow");
  if (error) fail("user_tags", error);
  return count ?? 0;
}

export async function fetchBlockedSources(client: Client): Promise<{ id: number; name: string }[]> {
  const { data, error } = await client.from("user_sources").select("source_id");
  if (error) fail("user_sources", error);
  const ids = data.map((r) => r.source_id);
  if (!ids.length) return [];
  const names = await client.from("sources").select("id, name").in("id", ids);
  if (names.error) fail("sources", names.error);
  const byId = new Map(names.data.map((s) => [s.id, s.name]));
  return ids.map((id) => ({ id, name: byId.get(id) ?? `Source ${id}` })).sort((a, b) => a.name.localeCompare(b.name));
}

export async function fetchProfile(client: Client): Promise<{ displayName: string | null; profileText: string | null }> {
  const { data, error } = await client.from("profiles").select("display_name, profile_text").maybeSingle();
  if (error) fail("profiles", error);
  return { displayName: data?.display_name ?? null, profileText: data?.profile_text ?? null };
}

export async function setFollowedTags(client: Client, tags: string[], profileText: string | null): Promise<void> {
  const { error } = await client.rpc("set_followed_tags", {
    p_tags: tags,
    ...(profileText === null ? {} : { p_profile_text: profileText }),
  });
  if (error) fail("set_followed_tags", error);
}

/** null clears the tag (neither followed nor blocked). */
export async function setTagState(client: Client, tag: string, state: TagState | null): Promise<void> {
  // The generated type says string, but the SQL accepts null to clear.
  const { error } = await client.rpc("set_tag_state", { p_tag: tag, p_state: state as string });
  if (error) fail("set_tag_state", error);
}

export async function setSourceBlocked(client: Client, sourceId: number, blocked: boolean): Promise<void> {
  const { error } = await client.rpc("set_source_blocked", { p_source_id: sourceId, p_blocked: blocked });
  if (error) fail("set_source_blocked", error);
}

/** '' clears the text. */
export async function updateProfileText(client: Client, profileText: string): Promise<void> {
  const { error } = await client.rpc("update_profile", { p_profile_text: profileText });
  if (error) fail("update_profile", error);
}

export async function fetchBookmarks(client: Client, offset: number): Promise<Card[]> {
  const { data, error } = await client.rpc("my_bookmarks", { p_limit: PAGE_SIZE, p_offset: offset });
  if (error) fail("my_bookmarks", error);
  return data.map(toCard);
}

export async function fetchHistory(client: Client, offset: number): Promise<Card[]> {
  const { data, error } = await client.rpc("my_history", { p_limit: PAGE_SIZE, p_offset: offset });
  if (error) fail("my_history", error);
  return data.map(toCard);
}

export async function deleteMyHistory(client: Client): Promise<number> {
  const { data, error } = await client.rpc("delete_my_history");
  if (error) fail("delete_my_history", error);
  return data;
}
