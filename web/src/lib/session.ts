"use client";

import { useSyncExternalStore } from "react";

import { AUTH_STORAGE_KEY } from "./auth-config";

// A cheap, synchronous view of the session that supabase-js keeps in localStorage. The
// header, card actions and the event tracker use it to know whether someone is signed in
// without loading supabase-js on every public page. The real client (lib/browser-client.ts)
// validates and refreshes the session when it is needed, and reports changes here.

export type SessionUser = {
  id: string;
  email: string | null;
  name: string;
  avatarUrl: string | null;
};

export type StoredSession = {
  user: SessionUser;
  accessToken: string;
  /** Unix seconds. */
  expiresAt: number;
};

const EVENT = "tnd-authchange";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

/** Parses the supabase-js session JSON; null for anything unexpected. */
export function parseStoredSession(raw: string | null): StoredSession | null {
  if (!raw) return null;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isObj(value) || !isObj(value.user)) return null;
  const id = str(value.user.id);
  const accessToken = str(value.access_token);
  if (!id || !accessToken) return null;
  const meta = isObj(value.user.user_metadata) ? value.user.user_metadata : {};
  const email = str(value.user.email);
  return {
    user: {
      id,
      email,
      name: str(meta.user_name) ?? str(meta.full_name) ?? str(meta.name) ?? email?.split("@")[0] ?? "Account",
      avatarUrl: str(meta.avatar_url),
    },
    accessToken,
    expiresAt: typeof value.expires_at === "number" ? value.expires_at : 0,
  };
}

function readRaw(): string | null {
  try {
    return localStorage.getItem(AUTH_STORAGE_KEY);
  } catch {
    return null;
  }
}

// useSyncExternalStore needs a stable snapshot: re-parse only when the raw value changes.
let cachedRaw: string | null = null;
let cached: StoredSession | null = null;

export function readStoredSession(): StoredSession | null {
  const raw = readRaw();
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    cached = parseStoredSession(raw);
  }
  return cached;
}

/** Called by the supabase client on every auth state change (same-tab storage writes fire no event). */
export function notifyAuthChange() {
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(onChange: () => void) {
  const onStorage = (e: StorageEvent) => {
    if (e.key === null || e.key === AUTH_STORAGE_KEY) onChange();
  };
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

const readUser = () => readStoredSession()?.user ?? null;

/**
 * The signed-in user, or null. Always null on the server and during hydration, so cached
 * public pages render the same HTML for everyone; signed-in UI appears right after.
 */
export function useSessionUser(): SessionUser | null {
  return useSyncExternalStore(subscribe, readUser, () => null);
}

/** false until hydrated; lets personalized pages tell "signed out" from "not known yet". */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false,
  );
}

function noopSubscribe() {
  return () => {};
}
