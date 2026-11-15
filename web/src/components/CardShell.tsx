"use client";

import type { ReactNode } from "react";

import { toggleReaction, useCardGone, useHiddenHere } from "@/lib/personal-store";

const UNDO_LABEL = { hide: "Hidden.", less_like_this: "Got it: fewer posts like this." } as const;

/**
 * Removes a card once the signed-in reader hides it (or blocks its tag or source). A card
 * hidden during this page view leaves a one-line "Undo" in its place. Renders the card
 * unchanged on the server and for signed-out readers.
 */
export function CardShell({
  id,
  tags,
  sourceId,
  children,
}: {
  id: string;
  tags: readonly string[];
  sourceId: number;
  children: ReactNode;
}) {
  const gone = useCardGone(id, tags, sourceId);
  const hiddenHere = useHiddenHere(id);
  if (!gone) return children;
  if (hiddenHere !== "hide" && hiddenHere !== "less_like_this") return null;
  return (
    <div
      role="status"
      className="flex min-h-14 items-center justify-between gap-3 rounded-2xl border border-dashed border-zinc-300 px-4 py-3 text-sm text-zinc-600 dark:border-zinc-700 dark:text-zinc-400"
    >
      <span>{UNDO_LABEL[hiddenHere]}</span>
      <button
        type="button"
        onClick={() => void toggleReaction(id, hiddenHere, false).catch(() => {})}
        className="rounded-full px-3 py-1 font-medium text-zinc-900 hover:bg-zinc-100 dark:text-zinc-100 dark:hover:bg-zinc-800"
      >
        Undo
      </button>
    </div>
  );
}
