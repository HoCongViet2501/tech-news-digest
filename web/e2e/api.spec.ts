import { createClient } from "@supabase/supabase-js";
import { expect, test } from "@playwright/test";

import type { Database } from "../src/lib/database.types";

import { ITEMS, itemId } from "./fixtures";

// The web's only credential is the anon (publishable) key. It must be able to call the
// read RPCs and nothing else.

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

test.skip(!url || !key, "NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY not set");

const anon = () =>
  createClient<Database>(url!, key!, { auth: { persistSession: false, autoRefreshToken: false } });

test("anon can call the read functions", async () => {
  const db = anon();
  const feed = await db.rpc("popular_feed", { p_sort: "discussed", p_limit: 5 });
  expect(feed.error).toBeNull();
  expect(feed.data!.length).toBeGreaterThan(0);

  const tags = await db.rpc("tag_list");
  expect(tags.error).toBeNull();

  const search = await db.rpc("search_items", { p_q: "kien truc" });
  expect(search.error).toBeNull();
  expect(search.data!.map((c) => c.title)).toContain(ITEMS.vietnamese.title);

  const item = await db.rpc("item_detail", { p_id: itemId(ITEMS.vietnamese.key) });
  expect(item.error).toBeNull();
  expect(item.data![0].description).toBe(ITEMS.vietnamese.description);
});

test("anon cannot write content tables", async () => {
  const db = anon();
  const id = itemId(ITEMS.vietnamese.key);

  const insert = await db.from("tags").insert({ slug: "e2e-anon", name: "x" });
  expect(insert.error?.code).toBe("42501");

  const update = await db.from("items").update({ title: "hacked" }).eq("id", id);
  expect(update.error?.code).toBe("42501");

  const del = await db.from("item_mentions").delete().eq("item_id", id);
  expect(del.error?.code).toBe("42501");

  // Fetch state of sources is not public (column grants).
  const secret = await db.from("sources").select("etag").limit(1);
  expect(secret.error?.code).toBe("42501");
});
