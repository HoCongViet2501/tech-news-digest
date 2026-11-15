"use client";

import { useEffect, useId, useRef, useState } from "react";

import type { TagRef } from "@/lib/card";
import type { Reaction } from "@/lib/personal";
import { blockSource, blockTag, toggleReaction, useItemState } from "@/lib/personal-store";
import { useSessionUser } from "@/lib/session";

import { BookmarkIcon, DownIcon, HideIcon, MoreIcon, UpIcon } from "./icons";

type Props = {
  id: string;
  tags: readonly TagRef[];
  source: { id: number; name: string };
  /** "card": compact row at the bottom of a grid card; "page": the item page toolbar. */
  variant?: "card" | "page";
};

const BTN =
  "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1.5 text-xs font-medium transition hover:bg-zinc-100 disabled:opacity-50 dark:hover:bg-zinc-800";
const ON: Record<"upvote" | "downvote" | "bookmark", string> = {
  upvote: "text-emerald-600 dark:text-emerald-400",
  downvote: "text-rose-600 dark:text-rose-400",
  bookmark: "text-amber-600 dark:text-amber-400",
};
const OFF = "text-zinc-600 dark:text-zinc-400";

/** Upvote, downvote, bookmark, hide and a menu (less like this, block tag/source). Signed-in only. */
export function CardActions({ id, tags, source, variant = "card" }: Props) {
  const user = useSessionUser();
  const { kinds } = useItemState(id, user !== null);
  const [error, setError] = useState<string | null>(null);
  if (!user) return null;
  const hidden = kinds.has("hide");

  const act = (kind: Reaction, on: boolean) => {
    setError(null);
    toggleReaction(id, kind, on).catch(() => setError("Could not save. Try again."));
  };
  const toggle = (kind: "upvote" | "downvote" | "bookmark", label: string, labelOn: string, Icon: typeof UpIcon) => {
    const on = kinds.has(kind);
    return (
      <button
        type="button"
        aria-pressed={on}
        aria-label={on ? labelOn : label}
        title={on ? labelOn : label}
        onClick={() => act(kind, !on)}
        className={`${BTN} ${on ? ON[kind] : OFF}`}
      >
        <Icon filled={on} className="size-4" />
        {variant === "page" ? <span>{on ? labelOn : label}</span> : null}
      </button>
    );
  };

  return (
    <div
      className={
        variant === "card"
          ? "relative z-10 flex items-center gap-0.5 border-t border-zinc-100 px-2 py-1 dark:border-zinc-800"
          : "flex flex-wrap items-center gap-1"
      }
      aria-label="Actions"
      role="group"
    >
      {toggle("upvote", "Upvote", "Upvoted", UpIcon)}
      {toggle("downvote", "Downvote", "Downvoted", DownIcon)}
      {toggle("bookmark", "Bookmark", "Bookmarked", BookmarkIcon)}
      {/* On a card, hiding removes it (with an Undo in its place); on the item page it toggles. */}
      <button
        type="button"
        aria-pressed={variant === "page" ? hidden : undefined}
        aria-label={hidden ? "Hidden from your feeds" : "Hide"}
        title={hidden ? "Hidden from your feeds. Click to unhide." : "Hide"}
        onClick={() => act("hide", !hidden)}
        className={`${BTN} ${hidden ? "text-zinc-900 dark:text-zinc-100" : OFF} ${variant === "card" ? "ml-auto" : ""}`}
      >
        <HideIcon className="size-4" />
        {variant === "page" ? <span>{hidden ? "Hidden from your feeds" : "Hide"}</span> : null}
      </button>
      <ActionMenu
        tags={tags}
        source={source}
        onLess={() => act("less_like_this", true)}
        onError={setError}
        up={variant === "card"}
      />
      {error ? (
        <span role="alert" className="px-2 text-xs text-rose-600 dark:text-rose-400">
          {error}
        </span>
      ) : null}
    </div>
  );
}

function ActionMenu({
  tags,
  source,
  onLess,
  onError,
  up,
}: {
  tags: readonly TagRef[];
  source: { id: number; name: string };
  onLess: () => void;
  onError: (msg: string | null) => void;
  /** Open upwards: grid cards clip overflow, and the row sits at their bottom. */
  up: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const run = (fn: () => Promise<void> | void) => () => {
    setOpen(false);
    onError(null);
    Promise.resolve(fn()).catch(() => onError("Could not save. Try again."));
  };

  const item =
    "block w-full rounded-lg px-3 py-2 text-left text-sm text-zinc-800 hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800";

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label="More actions"
        title="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((o) => !o)}
        className={`${BTN} ${OFF}`}
      >
        <MoreIcon className="size-4" />
      </button>
      {open ? (
        <div
          id={menuId}
          role="menu"
          className={`absolute right-0 z-30 w-60 ${up ? "bottom-full mb-1" : "top-full mt-1"} rounded-xl border border-zinc-200 bg-white p-1 shadow-lg dark:border-zinc-700 dark:bg-zinc-900`}
        >
          <button type="button" role="menuitem" className={item} onClick={run(onLess)}>
            Show less like this
          </button>
          {tags.map((t) => (
            <button key={t.slug} type="button" role="menuitem" className={item} onClick={run(() => blockTag(t.slug))}>
              Block #{t.slug}
            </button>
          ))}
          <button type="button" role="menuitem" className={item} onClick={run(() => blockSource(source.id))}>
            Block {source.name}
          </button>
        </div>
      ) : null}
    </div>
  );
}
