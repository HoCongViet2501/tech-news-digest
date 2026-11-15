"use client";

import { AUTH_STORAGE_KEY } from "./auth-config";
import { notifyAuthChange } from "./session";
import type { Client } from "./supabase";

// The signed-in client: supabase-js in the browser with the session in localStorage
// (docs/PLAN.md §13: personalized data goes browser → Supabase RPC with the user's JWT,
// never through a Vercel function). Loaded on demand, so public pages only pay for it
// when a signed-in reader acts on a card.

let client: Promise<Client> | null = null;

export class NotConfiguredError extends Error {
  constructor() {
    super("Supabase is not configured (NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY).");
  }
}

async function create(): Promise<Client> {
  const [{ createClient }, { supabaseEnv }] = await Promise.all([
    import("@supabase/supabase-js"),
    import("./supabase"),
  ]);
  const env = supabaseEnv();
  if (!env) throw new NotConfiguredError();
  const c: Client = createClient(env.url, env.key, {
    auth: {
      flowType: "pkce",
      storageKey: AUTH_STORAGE_KEY,
      persistSession: true,
      autoRefreshToken: true,
      // The OAuth code is exchanged explicitly on /auth/callback only.
      detectSessionInUrl: false,
    },
  });
  c.auth.onAuthStateChange(() => notifyAuthChange());
  return c;
}

export function getBrowserClient(): Promise<Client> {
  client ??= create().catch((err: unknown) => {
    client = null;
    throw err;
  });
  return client;
}

/** A fresh access token (refreshed by supabase-js when it is about to expire), or null. */
export async function getAccessToken(): Promise<string | null> {
  const c = await getBrowserClient();
  const { data } = await c.auth.getSession();
  return data.session?.access_token ?? null;
}

export async function signOut(): Promise<void> {
  const c = await getBrowserClient();
  // "local": this browser only. The refresh token is revoked server-side as well.
  await c.auth.signOut({ scope: "local" });
  try {
    localStorage.removeItem(AUTH_STORAGE_KEY);
  } catch {
    // Storage blocked: nothing was stored either.
  }
  notifyAuthChange();
}
