"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { getBrowserClient } from "@/lib/browser-client";
import { PAGE_SIZE } from "@/lib/card";
import { applyPage, initialPaging, nextCursor, type Cursor, type PagingState } from "@/lib/feed-paging";
import { countFollowedTags, fetchMyFeed, recordImpression } from "@/lib/personal";
import { takeFeedStale } from "@/lib/personal-store";
import { useHydrated, useSessionUser } from "@/lib/session";

import { CardPages, GridSkeleton } from "./CardPages";
import { RefreshIcon } from "./icons";
import { SignInPrompt } from "./SignInPrompt";

/** My Feed: the signed-in reader's ranked snapshot, paged from the browser via my_feed. */
export function MyFeed() {
  const hydrated = useHydrated();
  const user = useSessionUser();
  if (!hydrated) return <GridSkeleton />;
  if (!user) return <SignInPrompt next="/my-feed" title="My Feed" />;
  // Keyed by user: a different account starts from a clean state.
  return <MyFeedList key={user.id} />;
}

const PULL_THRESHOLD = 70;

function MyFeedList() {
  const router = useRouter();
  const [state, setState] = useState<PagingState>(initialPaging);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(() => Date.now());
  const stateRef = useRef(state);
  const busy = useRef(false);
  const generation = useRef(0);
  const impressions = useRef(new Set<string>());

  const load = useCallback(async (cursor: Cursor | null, refresh: boolean) => {
    if (busy.current && cursor !== null) return;
    busy.current = true;
    const gen = cursor === null ? ++generation.current : generation.current;
    setLoading(true);
    try {
      const client = await getBrowserClient();
      const rows = await fetchMyFeed(client, cursor, refresh);
      if (gen !== generation.current) return; // a refresh started meanwhile
      const next = applyPage(cursor === null ? initialPaging : stateRef.current, cursor, rows, PAGE_SIZE);
      if (cursor === null || next.restarted) {
        setNow(Date.now());
        impressions.current.clear();
      }
      stateRef.current = next;
      setState(next);
      setFailed(false);
      // One impression per rendered page of the snapshot.
      const key = rows.length ? `${rows[0].snapshotId}:${rows[0].pos}` : "";
      if (key && !impressions.current.has(key)) {
        impressions.current.add(key);
        recordImpression(client, rows[0].snapshotId, rows.map((r) => r.card.id)).catch(() => {});
      }
    } catch {
      if (gen === generation.current) setFailed(true);
    } finally {
      if (gen === generation.current) {
        busy.current = false;
        setLoading(false);
      }
    }
  }, []);

  const loaded = useRef(false);
  useEffect(() => {
    if (loaded.current) return; // StrictMode runs effects twice in dev
    loaded.current = true;
    // A follow/block/onboarding change since the last visit: re-rank.
    void load(null, takeFeedStale());
    // First sign-in: nobody to rank for yet, so ask for interests.
    getBrowserClient()
      .then(countFollowedTags)
      .then((n) => {
        if (n === 0) router.replace("/onboarding");
      })
      .catch(() => {});
  }, [load, router]);

  const refresh = useCallback(() => {
    window.scrollTo({ top: 0 });
    void load(null, true);
  }, [load]);

  const loadMore = useCallback(() => {
    if (!busy.current) void load(nextCursor(stateRef.current), false);
  }, [load]);

  const pull = usePullToRefresh(refresh);
  const first = state.pages.length === 0;

  return (
    <>
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold">My Feed</h1>
        <div className="ml-auto flex items-center gap-2">
          <Link
            href="/onboarding"
            className="rounded-full px-3 py-1.5 text-sm text-zinc-700 hover:bg-zinc-200 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            Edit interests
          </Link>
          <button
            type="button"
            onClick={refresh}
            disabled={loading && first}
            className="inline-flex items-center gap-1.5 rounded-full border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
          >
            <RefreshIcon className={`size-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </div>
      {pull > 0 ? (
        <div
          className="flex items-center justify-center text-sm text-zinc-600 dark:text-zinc-400"
          style={{ height: Math.min(pull, PULL_THRESHOLD * 1.5) }}
          aria-hidden="true"
        >
          {pull >= PULL_THRESHOLD ? "Release to refresh" : "Pull to refresh"}
        </div>
      ) : null}
      {first && loading && !failed ? (
        <GridSkeleton />
      ) : (
        <CardPages
          pages={state.pages}
          now={now}
          done={state.done}
          failed={failed}
          onNearEnd={loadMore}
          onRetry={() => (first ? refresh() : (setFailed(false), loadMore()))}
          empty={
            <p className="py-16 text-center text-zinc-600 dark:text-zinc-400">
              Nothing new for you right now.{" "}
              <Link href="/onboarding" className="underline">
                Follow more tags
              </Link>{" "}
              or check{" "}
              <Link href="/" className="underline">
                Popular
              </Link>
              .
            </p>
          }
        />
      )}
    </>
  );
}

/** Touch pull-down at the top of the page; returns the current pull distance (px). */
function usePullToRefresh(onRefresh: () => void): number {
  const [pull, setPull] = useState(0);
  useEffect(() => {
    let startY: number | null = null;
    let distance = 0;
    const onStart = (e: TouchEvent) => {
      startY = window.scrollY <= 0 && e.touches.length === 1 ? e.touches[0].clientY : null;
      distance = 0;
    };
    const onMove = (e: TouchEvent) => {
      if (startY === null) return;
      distance = Math.max(0, (e.touches[0].clientY - startY) / 2);
      setPull(distance);
    };
    const onEnd = () => {
      if (startY !== null && distance >= PULL_THRESHOLD) onRefresh();
      startY = null;
      distance = 0;
      setPull(0);
    };
    window.addEventListener("touchstart", onStart, { passive: true });
    window.addEventListener("touchmove", onMove, { passive: true });
    window.addEventListener("touchend", onEnd);
    window.addEventListener("touchcancel", onEnd);
    return () => {
      window.removeEventListener("touchstart", onStart);
      window.removeEventListener("touchmove", onMove);
      window.removeEventListener("touchend", onEnd);
      window.removeEventListener("touchcancel", onEnd);
    };
  }, [onRefresh]);
  return pull;
}
