"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { PAGE_SIZE, type Card, type FeedQuery } from "@/lib/card";
import type { Client } from "@/lib/supabase";

import { FEED_GRID, FeedCard } from "./FeedCard";

type Props = {
  /** The first page, rendered on the server (static HTML, not re-rendered on hydration). */
  children: ReactNode;
  /** Ids of the first page's cards, to drop duplicates from later pages. */
  firstIds: string[];
  query: FeedQuery;
  /** Ranking time (ISO) of the first page; card ages are relative to it. */
  asOf: string;
};

// Later pages go from the browser straight to Supabase RPC (anon key), so Vercel
// functions stay idle. supabase-js is loaded only when the first extra page is needed.
let browserClient: Promise<Client | null> | null = null;

async function loadPage(query: FeedQuery, offset: number): Promise<Card[]> {
  browserClient ??= import("@/lib/supabase").then((m) => m.anonClient());
  const [client, { fetchPage }] = await Promise.all([browserClient, import("@/lib/queries")]);
  if (!client) return [];
  return fetchPage(client, query, offset);
}

/**
 * Virtualized infinite list. Each fetched page is one chunk (a CSS grid block of
 * PAGE_SIZE cards). Chunks far outside the viewport are swapped for an empty block of
 * their measured height, so the DOM holds ~2–3 pages however far the reader scrolls.
 * The page after the last rendered one is prefetched as soon as the reader starts
 * scrolling, and appended when the end of the list comes within two screens.
 */
export function FeedList({ children, firstIds, query, asOf }: Props) {
  const now = Date.parse(asOf);
  // Pages after the first one.
  const [pages, setPages] = useState<Card[][]>([]);
  const [done, setDone] = useState(firstIds.length < PAGE_SIZE);
  const [failed, setFailed] = useState(false);
  const [nearEnd, setNearEnd] = useState(false);
  const sentinel = useRef<HTMLDivElement>(null);
  const busy = useRef(false);
  const prefetched = useRef<{ index: number; promise: Promise<Card[]> } | null>(null);

  const prefetch = useCallback(
    (index: number) => {
      if (prefetched.current?.index !== index) {
        const promise = loadPage(query, index * PAGE_SIZE);
        promise.catch(() => {}); // surfaced when awaited in loadMore
        prefetched.current = { index, promise };
      }
      return prefetched.current.promise;
    },
    [query],
  );

  const loadMore = useCallback(async () => {
    if (busy.current || done) return;
    busy.current = true;
    const index = pages.length + 1;
    try {
      const next = await prefetch(index);
      const seen = new Set([...firstIds, ...pages.flat().map((c) => c.id)]);
      setPages((p) => [...p, next.filter((c) => !seen.has(c.id))]);
      setFailed(false);
      if (next.length < PAGE_SIZE) setDone(true);
      else void prefetch(index + 1);
    } catch {
      prefetched.current = null;
      setFailed(true);
    } finally {
      busy.current = false;
    }
  }, [done, firstIds, pages, prefetch]);

  // Prefetch page 2 on the first scroll (not on load: keeps the initial load light).
  useEffect(() => {
    if (done) return;
    const onScroll = () => void prefetch(1);
    window.addEventListener("scroll", onScroll, { passive: true, once: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [done, prefetch]);

  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver(([entry]) => setNearEnd(entry.isIntersecting), {
      rootMargin: "200% 0px",
    });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (nearEnd && !failed) void loadMore();
  }, [nearEnd, failed, loadMore]);

  return (
    <div>
      <Chunk>{children}</Chunk>
      {pages.map((cards, i) =>
        cards.length ? (
          <Chunk key={i}>
            <div className={FEED_GRID}>
              {cards.map((card) => (
                <FeedCard key={card.id} card={card} now={now} />
              ))}
            </div>
          </Chunk>
        ) : null,
      )}
      <div ref={sentinel} aria-hidden="true" />
      <div className="py-8 text-center text-sm text-zinc-600 dark:text-zinc-400" aria-live="polite">
        {failed ? (
          <button
            type="button"
            onClick={() => setFailed(false)}
            className="rounded-full border border-zinc-300 px-4 py-1.5 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
          >
            Could not load more. Retry
          </button>
        ) : done ? (
          "You're all caught up."
        ) : (
          "Loading…"
        )}
      </div>
    </div>
  );
}

export function Chunk({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  // null = rendered; a number = collapsed to an empty block of that height.
  const [collapsed, setCollapsed] = useState<number | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([entry]) => setCollapsed(entry.isIntersecting ? null : el.getBoundingClientRect().height),
      { rootMargin: "150% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <div ref={ref} className="pb-4" style={collapsed === null ? undefined : { height: collapsed }}>
      {collapsed === null ? children : null}
    </div>
  );
}
