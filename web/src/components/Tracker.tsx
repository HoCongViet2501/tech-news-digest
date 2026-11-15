"use client";

import { useEffect } from "react";

import { getAccessToken } from "@/lib/browser-client";
import {
  dwellEvent,
  EventBatcher,
  MAX_BATCH,
  parsePendingDwell,
  recordEventsRequest,
  type PendingDwell,
  type TrackedEvent,
} from "@/lib/events";
import { readStoredSession } from "@/lib/session";

// Click and dwell tracking for signed-in readers (docs/PLAN.md §16 W4 step 4).
//
// - click: any link marked data-item-id (a card title, "Read the original", a
//   discussion link).
// - dwell: links also marked data-dwell leave for the article; the time the page then
//   stays hidden (another tab, or another page in this tab until back/forward) is sent
//   as a dwell event when the reader returns. The pending start lives in sessionStorage,
//   so a same-tab round trip that reloads this page still counts.
//
// Events are queued and sent in batches of ≤50 with fetch(..., {keepalive: true}) to the
// record_events RPC: sendBeacon cannot carry the Authorization header. The queue is
// flushed when the page is hidden or unloaded, every 30 s while visible, and when full.

const DWELL_KEY = "tnd-dwell";
const FLUSH_MS = 30_000;

type Pending = PendingDwell & { leftAt?: number };

function readPending(): Pending | null {
  try {
    const raw = sessionStorage.getItem(DWELL_KEY);
    const base = parsePendingDwell(raw);
    if (!base || !raw) return null;
    const leftAt = (JSON.parse(raw) as { leftAt?: unknown }).leftAt;
    return typeof leftAt === "number" ? { ...base, leftAt } : base;
  } catch {
    return null;
  }
}

function writePending(p: Pending | null) {
  try {
    if (p) sessionStorage.setItem(DWELL_KEY, JSON.stringify(p));
    else sessionStorage.removeItem(DWELL_KEY);
  } catch {
    // Storage blocked: no dwell for this click.
  }
}

async function send(events: TrackedEvent[]): Promise<void> {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  const stored = readStoredSession();
  if (!url || !key || !stored) return;
  let token = stored.accessToken;
  // A token about to expire is refreshed first, but only while the page is alive: on
  // pagehide there is no time for a refresh round trip, so the stored one is used as is.
  if (stored.expiresAt * 1000 < Date.now() + 10_000 && document.visibilityState === "visible") {
    token = (await getAccessToken()) ?? token;
  }
  const [input, init] = recordEventsRequest(url, key, token, events);
  const res = await fetch(input, init);
  if (!res.ok) throw new Error(`record_events: HTTP ${res.status}`);
}

const batcher = new EventBatcher(send);

function track(event: TrackedEvent) {
  if (!readStoredSession()) return;
  batcher.add(event);
  if (batcher.size >= MAX_BATCH) void batcher.flush();
}

/** On return to the page: turn a pending article visit into a dwell event. */
function settleDwell() {
  const p = readPending();
  if (!p) return;
  writePending(null);
  if (p.leftAt === undefined) return; // never left the page: no reading time to count
  const event = dwellEvent({ itemId: p.itemId, startedAt: p.leftAt }, Date.now());
  if (event) track(event);
}

function onLeave() {
  const p = readPending();
  if (p && p.leftAt === undefined) writePending({ ...p, leftAt: Date.now() });
  void batcher.flush();
}

function onClick(e: MouseEvent) {
  if (e.type === "auxclick" && e.button !== 1) return; // middle click opens a tab
  const link = e.target instanceof Element ? e.target.closest<HTMLAnchorElement>("a[data-item-id]") : null;
  const itemId = link?.dataset.itemId;
  if (!itemId || !readStoredSession()) return;
  track({ item_id: itemId, kind: "click" });
  if (link.dataset.dwell !== undefined) writePending({ itemId, startedAt: Date.now() });
}

export function Tracker() {
  useEffect(() => {
    // A reload or a new page load after visiting an article in this tab.
    settleDwell();

    const onVisibility = () => (document.visibilityState === "hidden" ? onLeave() : settleDwell());
    const onPageShow = (e: PageTransitionEvent) => {
      if (e.persisted) settleDwell(); // back/forward cache restore
    };
    document.addEventListener("click", onClick, true);
    document.addEventListener("auxclick", onClick, true);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", onLeave);
    window.addEventListener("pageshow", onPageShow);
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void batcher.flush();
    }, FLUSH_MS);
    return () => {
      document.removeEventListener("click", onClick, true);
      document.removeEventListener("auxclick", onClick, true);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", onLeave);
      window.removeEventListener("pageshow", onPageShow);
      clearInterval(timer);
    };
  }, []);
  return null;
}
