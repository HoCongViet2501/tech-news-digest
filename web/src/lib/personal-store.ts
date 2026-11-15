"use client";

import { useEffect, useSyncExternalStore } from "react";

import { FEED_STALE_KEY } from "./auth-config";
import { getBrowserClient } from "./browser-client";
import {
  fetchReactions,
  itemAction,
  setSourceBlocked,
  setTagState,
  type Reaction,
} from "./personal";

// Client-side state of the signed-in reader's choices on visible cards, shared by every
// card on the page: reactions (loaded in one batched query for the cards on screen),
// plus tags/sources blocked during this page view. Updates are optimistic and rolled
// back when the RPC fails.

type ItemState = { loaded: boolean; kinds: ReadonlySet<Reaction> };

const EMPTY: ItemState = { loaded: false, kinds: new Set() };

let items = new Map<string, ItemState>();
let blockedTags: ReadonlySet<string> = new Set();
let blockedSources: ReadonlySet<number> = new Set();
/** Items hidden (hide / less_like_this) during this page view, so they can offer "Undo". */
let hiddenHere = new Map<string, Reaction>();
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

function setItem(id: string, state: ItemState) {
  items = new Map(items).set(id, state);
  emit();
}

// ---- batched reaction loading ----

const requested = new Set<string>();
let pending: string[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;

async function loadPending() {
  timer = null;
  const ids = pending;
  pending = [];
  try {
    const client = await getBrowserClient();
    for (let i = 0; i < ids.length; i += 100) {
      const chunk = ids.slice(i, i + 100);
      const found = await fetchReactions(client, chunk);
      const next = new Map(items);
      for (const id of chunk) {
        // An optimistic change made while loading wins over the loaded state.
        if (next.get(id)?.loaded) continue;
        next.set(id, { loaded: true, kinds: found.get(id) ?? new Set() });
      }
      items = next;
      emit();
    }
  } catch {
    // Leave them unloaded: buttons still work, they just start "off".
    for (const id of ids) requested.delete(id);
  }
}

function request(id: string) {
  if (requested.has(id)) return;
  requested.add(id);
  pending.push(id);
  timer ??= setTimeout(loadPending, 50);
}

/** Reactions of one item; loads them (batched with the other visible cards) when `enabled`. */
export function useItemState(id: string, enabled: boolean): ItemState {
  useEffect(() => {
    if (enabled) request(id);
  }, [id, enabled]);
  return useSyncExternalStore(
    subscribe,
    () => items.get(id) ?? EMPTY,
    () => EMPTY,
  );
}

/** True when the reader hid this card or blocked its source or one of its tags in this view. */
export function useCardGone(id: string, tags: readonly string[], sourceId: number): boolean {
  return useSyncExternalStore(
    subscribe,
    () =>
      Boolean(items.get(id)?.kinds.has("hide")) ||
      Boolean(items.get(id)?.kinds.has("less_like_this")) ||
      blockedSources.has(sourceId) ||
      tags.some((t) => blockedTags.has(t)),
    () => false,
  );
}

export function markFeedStale() {
  try {
    localStorage.setItem(FEED_STALE_KEY, "1");
  } catch {
    // Storage blocked: the next snapshot (30 min) picks the change up anyway.
  }
}

/** Reads and clears the "re-rank on next load" flag. */
export function takeFeedStale(): boolean {
  try {
    const stale = localStorage.getItem(FEED_STALE_KEY) === "1";
    localStorage.removeItem(FEED_STALE_KEY);
    return stale;
  } catch {
    return false;
  }
}

/** Optimistic toggle; upvote and downvote exclude each other. Throws after rolling back. */
export async function toggleReaction(id: string, kind: Reaction, on: boolean): Promise<void> {
  const before = items.get(id) ?? EMPTY;
  const kinds = new Set(before.kinds);
  if (on) {
    kinds.add(kind);
    if (kind === "upvote") kinds.delete("downvote");
    if (kind === "downvote") kinds.delete("upvote");
  } else {
    kinds.delete(kind);
  }
  const hiddenBefore = hiddenHere;
  if (kind === "hide" || kind === "less_like_this") {
    hiddenHere = new Map(hiddenHere);
    if (on) hiddenHere.set(id, kind);
    else hiddenHere.delete(id);
  }
  setItem(id, { loaded: true, kinds });
  try {
    // Hidden items are filtered when each page is sliced: no re-rank needed.
    await itemAction(await getBrowserClient(), id, kind, on);
  } catch (err) {
    hiddenHere = hiddenBefore;
    setItem(id, before);
    throw err;
  }
}

/** The hide-like reaction applied to this item during this page view, if any. */
export function useHiddenHere(id: string): Reaction | null {
  return useSyncExternalStore(
    subscribe,
    () => hiddenHere.get(id) ?? null,
    () => null,
  );
}

export async function blockTag(slug: string): Promise<void> {
  const before = blockedTags;
  blockedTags = new Set(before).add(slug);
  emit();
  try {
    await setTagState(await getBrowserClient(), slug, "block");
    markFeedStale();
  } catch (err) {
    blockedTags = before;
    emit();
    throw err;
  }
}

export async function blockSource(sourceId: number): Promise<void> {
  const before = blockedSources;
  blockedSources = new Set(before).add(sourceId);
  emit();
  try {
    await setSourceBlocked(await getBrowserClient(), sourceId, true);
    markFeedStale();
  } catch (err) {
    blockedSources = before;
    emit();
    throw err;
  }
}

/** Forget everything (sign-out, or another account signed in). */
export function resetPersonalStore() {
  items = new Map();
  blockedTags = new Set();
  blockedSources = new Set();
  hiddenHere = new Map();
  requested.clear();
  pending = [];
  emit();
}
