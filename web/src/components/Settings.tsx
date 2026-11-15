"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { getBrowserClient, signOut } from "@/lib/browser-client";
import type { TagInfo } from "@/lib/data";
import {
  deleteMyHistory,
  fetchBlockedSources,
  fetchMyTags,
  fetchProfile,
  setSourceBlocked,
  setTagState,
  updateProfileText,
  type TagState,
} from "@/lib/personal";
import { markFeedStale, resetPersonalStore } from "@/lib/personal-store";
import { useHydrated, useSessionUser, type SessionUser } from "@/lib/session";

import { SignInPrompt } from "./SignInPrompt";

const PROFILE_MAX = 2000;

export function Settings({ tags }: { tags: TagInfo[] }) {
  const hydrated = useHydrated();
  const user = useSessionUser();
  if (!hydrated) return null;
  if (!user) return <SignInPrompt next="/settings" title="Settings" />;
  return <SettingsForm key={user.id} user={user} tags={tags} />;
}

type Loaded = {
  tags: { tag: string; state: TagState }[];
  sources: { id: number; name: string }[];
  profileText: string;
};

async function loadSettings(): Promise<Loaded> {
  const c = await getBrowserClient();
  const [mine, sources, profile] = await Promise.all([fetchMyTags(c), fetchBlockedSources(c), fetchProfile(c)]);
  return { tags: mine, sources, profileText: profile.profileText ?? "" };
}

function SettingsForm({ user, tags }: { user: SessionUser; tags: TagInfo[] }) {
  const router = useRouter();
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const names = new Map(tags.map((t) => [t.slug, t.name]));

  const reload = useCallback(async () => {
    setData(await loadSettings());
  }, []);

  useEffect(() => {
    let cancelled = false;
    loadSettings().then(
      (loaded) => !cancelled && setData(loaded),
      () => !cancelled && setError("Could not load your settings."),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const run = (fn: (c: Awaited<ReturnType<typeof getBrowserClient>>) => Promise<unknown>) => async () => {
    setError(null);
    try {
      await fn(await getBrowserClient());
      markFeedStale();
      await reload();
    } catch {
      setError("Could not save. Try again.");
    }
  };

  if (!data) {
    return error ? <p role="alert">{error}</p> : <div className="h-64 animate-pulse rounded-2xl bg-zinc-200 dark:bg-zinc-800" />;
  }
  const follows = data.tags.filter((t) => t.state === "follow");
  const blocks = data.tags.filter((t) => t.state === "block");
  const blockable = tags.filter((t) => !data.tags.some((m) => m.tag === t.slug));

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-2xl font-bold">Settings</h1>
      {error ? (
        <p role="alert" className="text-sm text-rose-600 dark:text-rose-400">
          {error}
        </p>
      ) : null}

      <Section
        title="Followed tags"
        action={
          <Link href="/onboarding" className="text-sm underline">
            Edit interests
          </Link>
        }
      >
        {follows.length ? (
          <ChipList>
            {follows.map((t) => (
              <Chip key={t.tag} label={names.get(t.tag) ?? t.tag} actionLabel={`Unfollow ${t.tag}`} onAction={run((c) => setTagState(c, t.tag, null))} />
            ))}
          </ChipList>
        ) : (
          <Muted>You follow no tags yet.</Muted>
        )}
        {follows.length > 0 && follows.length < 3 ? (
          <Muted>Follow at least 3 tags for a useful feed.</Muted>
        ) : null}
      </Section>

      <Section title="Blocked tags">
        {blocks.length ? (
          <ChipList>
            {blocks.map((t) => (
              <Chip key={t.tag} label={`#${t.tag}`} actionLabel={`Unblock #${t.tag}`} onAction={run((c) => setTagState(c, t.tag, null))} />
            ))}
          </ChipList>
        ) : (
          <Muted>No blocked tags. Block one from a card&apos;s ⋯ menu, or here:</Muted>
        )}
        <BlockTagForm tags={blockable} onBlock={(slug) => run((c) => setTagState(c, slug, "block"))()} />
      </Section>

      <Section title="Blocked sources">
        {data.sources.length ? (
          <ChipList>
            {data.sources.map((s) => (
              <Chip key={s.id} label={s.name} actionLabel={`Unblock ${s.name}`} onAction={run((c) => setSourceBlocked(c, s.id, false))} />
            ))}
          </ChipList>
        ) : (
          <Muted>No blocked sources. Block one from a card&apos;s ⋯ menu.</Muted>
        )}
      </Section>

      <ProfileText initial={data.profileText} />

      <DeleteHistory />

      <Section title="Account">
        <p className="text-sm">
          Signed in as <span className="font-medium">{user.email ?? user.name}</span>
        </p>
        <button
          type="button"
          onClick={async () => {
            await signOut().catch(() => {});
            resetPersonalStore();
            router.push("/");
          }}
          className="mt-3 rounded-full border border-zinc-300 px-4 py-1.5 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
        >
          Sign out
        </button>
      </Section>
    </div>
  );
}

function Section({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="font-semibold">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

const Muted = ({ children }: { children: ReactNode }) => (
  <p className="text-sm text-zinc-600 dark:text-zinc-400">{children}</p>
);

const ChipList = ({ children }: { children: ReactNode }) => <ul className="flex flex-wrap gap-1.5">{children}</ul>;

function Chip({ label, actionLabel, onAction }: { label: string; actionLabel: string; onAction: () => void }) {
  return (
    <li className="inline-flex items-center gap-1 rounded-full border border-zinc-300 py-0.5 pr-1 pl-3 text-sm dark:border-zinc-700">
      {label}
      <button
        type="button"
        aria-label={actionLabel}
        title={actionLabel}
        onClick={onAction}
        className="flex size-6 items-center justify-center rounded-full text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
      >
        ×
      </button>
    </li>
  );
}

function BlockTagForm({ tags, onBlock }: { tags: TagInfo[]; onBlock: (slug: string) => void }) {
  const [slug, setSlug] = useState("");
  return (
    <form
      className="mt-3 flex gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (slug) onBlock(slug);
        setSlug("");
      }}
    >
      <select
        value={slug}
        onChange={(e) => setSlug(e.target.value)}
        aria-label="Tag to block"
        className="min-w-0 flex-1 rounded-full border border-zinc-300 bg-transparent px-3 py-1.5 text-sm dark:border-zinc-700"
      >
        <option value="">Choose a tag…</option>
        {tags.map((t) => (
          <option key={t.slug} value={t.slug}>
            #{t.slug}
          </option>
        ))}
      </select>
      <button
        type="submit"
        disabled={!slug}
        className="rounded-full border border-zinc-300 px-4 py-1.5 text-sm hover:bg-zinc-100 disabled:opacity-40 dark:border-zinc-700 dark:hover:bg-zinc-800"
      >
        Block
      </button>
    </form>
  );
}

function ProfileText({ initial }: { initial: string }) {
  const [text, setText] = useState(initial);
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  return (
    <Section title="About you">
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setStatus("saving");
          try {
            await updateProfileText(await getBrowserClient(), text.trim());
            markFeedStale();
            setStatus("saved");
          } catch {
            setStatus("error");
          }
        }}
      >
        <label className="block text-sm text-zinc-600 dark:text-zinc-400" htmlFor="profile-text">
          Free text about your work and interests. It tunes your feed after the next update (within a few hours).
        </label>
        <textarea
          id="profile-text"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setStatus("idle");
          }}
          maxLength={PROFILE_MAX}
          rows={3}
          className="mt-2 w-full rounded-xl border border-zinc-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-zinc-500 dark:border-zinc-700"
        />
        <div className="mt-2 flex items-center gap-3">
          <button
            type="submit"
            disabled={status === "saving" || text.trim() === initial.trim()}
            className="rounded-full bg-zinc-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            Save
          </button>
          <span className="text-sm text-zinc-600 dark:text-zinc-400" aria-live="polite">
            {status === "saved" ? "Saved." : status === "error" ? "Could not save. Try again." : ""}
          </span>
        </div>
      </form>
    </Section>
  );
}

function DeleteHistory() {
  const [step, setStep] = useState<"idle" | "confirm" | "deleting" | "error">("idle");
  const [deleted, setDeleted] = useState<number | null>(null);
  return (
    <Section title="Reading history">
      <Muted>
        Your clicks, reading time and feed impressions help rank My Feed. Deleting them keeps your votes, bookmarks,
        hidden items, follows and blocks.{" "}
        <Link href="/history" className="underline">
          See your history
        </Link>
      </Muted>
      {step === "confirm" || step === "deleting" ? (
        <div role="alertdialog" aria-label="Confirm deleting history" className="mt-3 rounded-xl bg-rose-50 p-3 dark:bg-rose-950/40">
          <p className="text-sm">Delete your whole reading history? This cannot be undone.</p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              disabled={step === "deleting"}
              onClick={async () => {
                setStep("deleting");
                try {
                  setDeleted(await deleteMyHistory(await getBrowserClient()));
                  markFeedStale();
                  setStep("idle");
                } catch {
                  setStep("error");
                }
              }}
              className="rounded-full bg-rose-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-rose-700 disabled:opacity-50"
            >
              {step === "deleting" ? "Deleting…" : "Yes, delete"}
            </button>
            <button
              type="button"
              onClick={() => setStep("idle")}
              className="rounded-full border border-zinc-300 px-4 py-1.5 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex items-center gap-3">
          <button
            type="button"
            onClick={() => {
              setDeleted(null);
              setStep("confirm");
            }}
            className="rounded-full border border-rose-300 px-4 py-1.5 text-sm text-rose-700 hover:bg-rose-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950/40"
          >
            Delete my history
          </button>
          <span className="text-sm text-zinc-600 dark:text-zinc-400" aria-live="polite">
            {step === "error"
              ? "Could not delete. Try again."
              : deleted !== null
                ? `Deleted ${deleted} ${deleted === 1 ? "event" : "events"}.`
                : ""}
          </span>
        </div>
      )}
    </Section>
  );
}
