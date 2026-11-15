"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { authErrorMessage, safeNext } from "@/lib/auth-errors";
import { passwordLoginEnabled } from "@/lib/auth-config";
import { getBrowserClient } from "@/lib/browser-client";
import { useHydrated, useSessionUser } from "@/lib/session";

function nextParam(): string | null {
  return new URLSearchParams(window.location.search).get("next");
}

/** GitHub sign-in (Supabase Auth, PKCE); email/password only for local e2e runs. */
export function SignIn() {
  const hydrated = useHydrated();
  const user = useSessionUser();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (hydrated && user) {
    return (
      <Box title="You're signed in">
        <p className="text-zinc-600 dark:text-zinc-400">Signed in as {user.email ?? user.name}.</p>
        <Link href="/my-feed" className={PRIMARY}>
          Go to My Feed
        </Link>
      </Box>
    );
  }

  async function github() {
    setBusy(true);
    setError(null);
    try {
      const client = await getBrowserClient();
      const callback = new URL("/auth/callback", window.location.origin);
      const next = nextParam();
      if (next) callback.searchParams.set("next", safeNext(next));
      // Redirects away to GitHub; the code comes back to /auth/callback.
      const { error: err } = await client.auth.signInWithOAuth({
        provider: "github",
        options: { redirectTo: callback.toString() },
      });
      if (err) throw err;
    } catch (err) {
      setError(authErrorMessage(err as { message?: string; status?: number }));
      setBusy(false);
    }
  }

  return (
    <Box title="Sign in">
      <p className="text-zinc-600 dark:text-zinc-400">
        Sign in to get My Feed, bookmarks and reading history. Accounts are by invitation only.
      </p>
      <button type="button" onClick={github} disabled={busy || !hydrated} className={`${PRIMARY} gap-2`}>
        <GitHubMark />
        Continue with GitHub
      </button>
      {error ? <ErrorNote>{error}</ErrorNote> : null}
      {hydrated && passwordLoginEnabled() ? <PasswordForm /> : null}
    </Box>
  );
}

function PasswordForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const mode = (e.nativeEvent as SubmitEvent).submitter?.getAttribute("value") === "signup" ? "signup" : "signin";
    setBusy(true);
    setError(null);
    try {
      const client = await getBrowserClient();
      const { error: err } =
        mode === "signup"
          ? await client.auth.signUp({ email, password })
          : await client.auth.signInWithPassword({ email, password });
      if (err) throw err;
      router.push(safeNext(nextParam()));
    } catch (err) {
      setError(authErrorMessage(err as { message?: string; status?: number; code?: string }));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-8 space-y-3 border-t border-dashed border-zinc-300 pt-6 text-left dark:border-zinc-700">
      <p className="text-xs font-semibold tracking-wide text-amber-700 uppercase dark:text-amber-400">
        Local development only
      </p>
      <label className="block text-sm">
        Email
        <input
          type="email"
          required
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="mt-1 w-full rounded-xl border border-zinc-300 bg-transparent px-3 py-2 dark:border-zinc-700"
        />
      </label>
      <label className="block text-sm">
        Password
        <input
          type="password"
          required
          minLength={6}
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mt-1 w-full rounded-xl border border-zinc-300 bg-transparent px-3 py-2 dark:border-zinc-700"
        />
      </label>
      <div className="flex gap-2">
        <button type="submit" value="signin" disabled={busy} className={SECONDARY}>
          Sign in with password
        </button>
        <button type="submit" value="signup" disabled={busy} className={SECONDARY}>
          Create account
        </button>
      </div>
      {error ? <ErrorNote>{error}</ErrorNote> : null}
    </form>
  );
}

export const PRIMARY =
  "mt-6 inline-flex items-center justify-center rounded-full bg-zinc-900 px-5 py-2.5 text-sm font-medium text-white hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300";
const SECONDARY =
  "rounded-full border border-zinc-300 px-4 py-1.5 text-sm hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800";

export function Box({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-md rounded-2xl border border-zinc-200 bg-white p-6 text-center shadow-sm sm:mt-10 dark:border-zinc-800 dark:bg-zinc-900">
      <h1 className="mb-2 text-2xl font-bold">{title}</h1>
      {children}
    </div>
  );
}

export function ErrorNote({ children }: { children: React.ReactNode }) {
  return (
    <p role="alert" className="mt-4 rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:bg-rose-950/50 dark:text-rose-300">
      {children}
    </p>
  );
}

function GitHubMark() {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="size-4" fill="currentColor">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}
