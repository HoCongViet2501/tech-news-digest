import { createClient, type SupabaseClient } from "@supabase/supabase-js";

import type { Database } from "./database.types";

export type Client = SupabaseClient<Database>;

/**
 * Anon (publishable) key only: the web always runs as anon or as the signed-in user,
 * so RLS and the function grants decide what it can read. There is no secret key.
 */
export function supabaseEnv(): { url: string; key: string } | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  return url && key ? { url, key } : null;
}

/** A stateless client (no session storage, no token refresh); null when not configured. */
export function anonClient(): Client | null {
  const env = supabaseEnv();
  if (!env) return null;
  return createClient<Database>(env.url, env.key, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
    // Results are cached by the "use cache" loaders (lib/data.ts). Keep Next's fetch
    // cache out of it: it would add a second, build-persistent copy of each RPC response.
    global: { fetch: (input, init) => fetch(input, { ...init, cache: "no-store" }) },
  });
}
