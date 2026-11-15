"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { getBrowserClient } from "@/lib/browser-client";
import { PAGE_SIZE, type Card } from "@/lib/card";
import { fetchBookmarks, fetchHistory } from "@/lib/personal";
import type { Client } from "@/lib/supabase";
import { useHydrated, useSessionUser } from "@/lib/session";

import { CardPages, GridSkeleton } from "./CardPages";
import { SignInPrompt } from "./SignInPrompt";

type Kind = "bookmarks" | "history";

const CONFIG: Record<Kind, { title: string; path: string; fetch: (c: Client, offset: number) => Promise<Card[]> }> = {
  bookmarks: { title: "Bookmarks", path: "/bookmarks", fetch: fetchBookmarks },
  history: { title: "Reading history", path: "/history", fetch: fetchHistory },
};

/** Bookmarks or reading history: offset-paged RPCs, loaded in the browser. */
export function CardCollection({ kind, empty, actions }: { kind: Kind; empty: ReactNode; actions?: ReactNode }) {
  const hydrated = useHydrated();
  const user = useSessionUser();
  const { title, path } = CONFIG[kind];
  if (!hydrated) return <GridSkeleton />;
  if (!user) return <SignInPrompt next={path} title={title} />;
  return (
    <>
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold">{title}</h1>
        {actions ? <div className="ml-auto">{actions}</div> : null}
      </div>
      <Collection key={user.id} kind={kind} empty={empty} />
    </>
  );
}

function Collection({ kind, empty }: { kind: Kind; empty: ReactNode }) {
  const [pages, setPages] = useState<Card[][]>([]);
  const [done, setDone] = useState(false);
  const [failed, setFailed] = useState(false);
  const [now] = useState(() => Date.now());
  const busy = useRef(false);
  const offset = useRef(0);
  const seen = useRef(new Set<string>());

  const loadMore = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      const cards = await CONFIG[kind].fetch(await getBrowserClient(), offset.current);
      offset.current += cards.length;
      const fresh = cards.filter((c) => !seen.current.has(c.id));
      for (const c of fresh) seen.current.add(c.id);
      setPages((p) => [...p, fresh]);
      setDone(cards.length < PAGE_SIZE);
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      busy.current = false;
    }
  }, [kind]);

  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void loadMore();
  }, [loadMore]);

  if (!pages.length && !done && !failed) return <GridSkeleton />;
  return (
    <CardPages
      pages={pages}
      now={now}
      done={done}
      failed={failed}
      onNearEnd={() => void loadMore()}
      onRetry={() => {
        setFailed(false);
        void loadMore();
      }}
      empty={empty}
      endLabel="That's everything."
    />
  );
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return (
    <p className="py-16 text-center text-zinc-600 dark:text-zinc-400">
      {children}{" "}
      <Link href="/my-feed" className="underline">
        Back to My Feed
      </Link>
    </p>
  );
}
