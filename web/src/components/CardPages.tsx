"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

import type { Card } from "@/lib/card";

import { FEED_GRID, FeedCard } from "./FeedCard";
import { Chunk } from "./FeedList";

type Props = {
  pages: Card[][];
  /** Ages are relative to this time (ms). */
  now: number;
  done: boolean;
  failed: boolean;
  /** Called when the end of the list comes within two screens. */
  onNearEnd: () => void;
  onRetry: () => void;
  empty: ReactNode;
  endLabel?: string;
};

/**
 * Infinite grid for pages loaded in the browser (My Feed, bookmarks, history). Same
 * virtualization as the public FeedList: one Chunk per page, collapsed when far away.
 */
export function CardPages({ pages, now, done, failed, onNearEnd, onRetry, empty, endLabel }: Props) {
  const sentinel = useRef<HTMLDivElement>(null);
  const [nearEnd, setNearEnd] = useState(false);
  const total = pages.reduce((n, p) => n + p.length, 0);

  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver(([entry]) => setNearEnd(entry.isIntersecting), { rootMargin: "200% 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (nearEnd && !done && !failed) onNearEnd();
  }, [nearEnd, done, failed, onNearEnd, total]);

  return (
    <div>
      {done && total === 0 ? empty : null}
      {pages.map((cards, i) =>
        cards.length ? (
          <Chunk key={i}>
            <div className={FEED_GRID}>
              {cards.map((card, j) => (
                <FeedCard key={card.id} card={card} now={now} eager={i === 0 && j < 2} />
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
            onClick={onRetry}
            className="rounded-full border border-zinc-300 px-4 py-1.5 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
          >
            Could not load. Retry
          </button>
        ) : done ? (
          total > 0 ? (endLabel ?? "You're all caught up.") : null
        ) : (
          "Loading…"
        )}
      </div>
    </div>
  );
}

export function GridSkeleton() {
  return (
    <div className={FEED_GRID} aria-busy="true">
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="h-80 animate-pulse rounded-2xl bg-zinc-200 dark:bg-zinc-800" />
      ))}
    </div>
  );
}
