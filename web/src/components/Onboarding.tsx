"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { getBrowserClient } from "@/lib/browser-client";
import type { TagInfo } from "@/lib/data";
import { fetchMyTags, fetchProfile, setFollowedTags } from "@/lib/personal";
import { useHydrated, useSessionUser } from "@/lib/session";

import { SignInPrompt } from "./SignInPrompt";

export const MIN_TAGS = 3;
const PROFILE_MAX = 2000;

/** Groups tags under their root (a tag whose parent is unknown counts as a root). */
export function groupByRoot(tags: TagInfo[]): { root: TagInfo; children: TagInfo[] }[] {
  const slugs = new Set(tags.map((t) => t.slug));
  return tags
    .filter((t) => !t.parent || !slugs.has(t.parent))
    .map((root) => ({ root, children: tags.filter((t) => t.parent === root.slug) }));
}

/** Onboarding / "edit interests": at least 3 followed tags plus optional free text. */
export function Onboarding({ tags }: { tags: TagInfo[] }) {
  const hydrated = useHydrated();
  const user = useSessionUser();
  if (!hydrated) return null;
  if (!user) return <SignInPrompt next="/onboarding" title="Pick your interests" />;
  return <OnboardingForm key={user.id} tags={tags} />;
}

function OnboardingForm({ tags }: { tags: TagInfo[] }) {
  const router = useRouter();
  const groups = useMemo(() => groupByRoot(tags), [tags]);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [text, setText] = useState("");
  const [returning, setReturning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Prefill with the current choices ("edit interests").
  useEffect(() => {
    let cancelled = false;
    getBrowserClient()
      .then((c) => Promise.all([fetchMyTags(c), fetchProfile(c)]))
      .then(([mine, profile]) => {
        if (cancelled) return;
        const follows = mine.filter((t) => t.state === "follow").map((t) => t.tag);
        if (follows.length) {
          setSelected(new Set(follows));
          setReturning(true);
        }
        if (profile.profileText) setText(profile.profileText);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = (slug: string) =>
    setSelected((s) => {
      const next = new Set(s);
      if (!next.delete(slug)) next.add(slug);
      return next;
    });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (selected.size < MIN_TAGS) return;
    setSaving(true);
    setError(null);
    try {
      await setFollowedTags(await getBrowserClient(), [...selected], text.trim());
      router.push("/my-feed");
    } catch {
      setError("Could not save your interests. Try again.");
      setSaving(false);
    }
  }

  const missing = Math.max(0, MIN_TAGS - selected.size);

  return (
    <form onSubmit={submit} className="mx-auto max-w-4xl">
      <h1 className="text-2xl font-bold">{returning ? "Edit your interests" : "Welcome! What do you want to read about?"}</h1>
      <p className="mt-1 text-zinc-600 dark:text-zinc-400">
        Pick at least {MIN_TAGS} tags. A parent tag covers everything under it. You can change this any time.
      </p>

      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        {groups.map(({ root, children }) => (
          <fieldset
            key={root.slug}
            className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
          >
            <legend className="sr-only">{root.name}</legend>
            <div className="flex flex-wrap gap-1.5">
              <TagChip tag={root} on={selected.has(root.slug)} onToggle={toggle} strong />
              {children.map((c) => (
                <TagChip key={c.slug} tag={c} on={selected.has(c.slug)} onToggle={toggle} />
              ))}
            </div>
            {root.description ? (
              <p className="mt-2 text-xs text-zinc-600 dark:text-zinc-400">{root.description}</p>
            ) : null}
          </fieldset>
        ))}
      </div>

      <label className="mt-6 block">
        <span className="font-medium">Anything else? (optional)</span>
        <span className="mt-0.5 block text-sm text-zinc-600 dark:text-zinc-400">
          A few words about your work or what you want to follow, e.g. &quot;Backend in Go and Postgres, moving to
          Kubernetes&quot;. It tunes your feed within a few hours.
        </span>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={PROFILE_MAX}
          rows={3}
          className="mt-2 w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-900"
        />
      </label>

      <div className="sticky bottom-0 mt-6 flex items-center gap-3 border-t border-zinc-200 bg-zinc-50/95 py-3 dark:border-zinc-800 dark:bg-zinc-950/95">
        <span className="text-sm text-zinc-600 dark:text-zinc-400" aria-live="polite">
          {missing ? `Pick ${missing} more` : `${selected.size} tags selected`}
        </span>
        {error ? (
          <span role="alert" className="text-sm text-rose-600 dark:text-rose-400">
            {error}
          </span>
        ) : null}
        <button
          type="submit"
          disabled={missing > 0 || saving}
          className="ml-auto rounded-full bg-zinc-900 px-5 py-2 text-sm font-medium text-white hover:bg-zinc-700 disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
        >
          {saving ? "Saving…" : returning ? "Save" : "Show my feed"}
        </button>
      </div>
    </form>
  );
}

function TagChip({
  tag,
  on,
  onToggle,
  strong,
}: {
  tag: TagInfo;
  on: boolean;
  onToggle: (slug: string) => void;
  strong?: boolean;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={() => onToggle(tag.slug)}
      title={tag.description ?? undefined}
      className={`rounded-full border px-3 py-1 text-sm transition ${strong ? "font-semibold" : ""} ${
        on
          ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900"
          : "border-zinc-300 hover:border-zinc-500 dark:border-zinc-700 dark:hover:border-zinc-500"
      }`}
    >
      {on ? <span aria-hidden="true">✓ </span> : null}
      {tag.name}
    </button>
  );
}
