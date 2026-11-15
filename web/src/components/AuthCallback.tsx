"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { authErrorMessage, redirectError, safeNext, type AuthFailure } from "@/lib/auth-errors";
import { getBrowserClient } from "@/lib/browser-client";
import { countFollowedTags } from "@/lib/personal";

import { Box, ErrorNote, PRIMARY } from "./SignIn";

class MissingCode extends Error {}

/**
 * OAuth return page (PKCE): exchanges ?code= for a session in the browser, then goes to
 * onboarding on the first sign-in (no followed tags) or to My Feed. A static page, so a
 * sign-in costs no Vercel function invocation.
 */
export function AuthCallback() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return; // StrictMode: the code can be exchanged only once
    started.current = true;
    const { search, hash } = window.location;
    const params = new URLSearchParams(search);
    (async () => {
      const failed = redirectError(search, hash);
      if (failed) throw failed;
      const code = params.get("code");
      if (!code) throw new MissingCode();
      const client = await getBrowserClient();
      const { error: err } = await client.auth.exchangeCodeForSession(code);
      if (err) throw err;
      const follows = await countFollowedTags(client).catch(() => 1);
      router.replace(follows === 0 ? "/onboarding" : safeNext(params.get("next")));
    })().catch((err: unknown) =>
      setError(
        err instanceof MissingCode
          ? "This sign-in link is incomplete or was already used. Please sign in again."
          : authErrorMessage(err as AuthFailure),
      ),
    );
  }, [router]);

  if (!error) {
    return (
      <Box title="Signing you in…">
        <p className="text-zinc-600 dark:text-zinc-400" aria-live="polite">
          One moment.
        </p>
      </Box>
    );
  }
  return (
    <Box title="Could not sign you in">
      <ErrorNote>{error}</ErrorNote>
      <Link href="/sign-in" className={PRIMARY}>
        Try again
      </Link>
      <p className="mt-4 text-sm">
        <Link href="/" className="underline">
          Browse Popular
        </Link>
      </p>
    </Box>
  );
}
