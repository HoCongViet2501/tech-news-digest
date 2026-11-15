// Turns Supabase Auth errors into reader-facing text. Sign-up is invite-only: the DB's
// "before user created" hook rejects other accounts with 403 "Sign-up is by invitation
// only." (supabase/migrations/*_w4_accounts.sql). After an OAuth redirect the same error
// arrives as error / error_code / error_description query (or hash) parameters.

export const INVITE_ONLY_MESSAGE =
  "This site is invite-only, and this account is not on the list. Ask the owner for an invite, then sign in again.";

export type AuthFailure = { message?: string | null; status?: number | null; code?: string | null };

export function isInviteOnlyError(err: AuthFailure): boolean {
  return /invit/i.test(err.message ?? "") || (err.status === 403 && err.code !== "over_request_rate_limit");
}

export function authErrorMessage(err: AuthFailure): string {
  if (isInviteOnlyError(err)) return INVITE_ONLY_MESSAGE;
  if (err.code === "invalid_credentials" || /invalid login credentials/i.test(err.message ?? "")) {
    return "Wrong email or password.";
  }
  if (err.status === 429 || /rate limit/i.test(err.message ?? "")) {
    return "Too many attempts. Wait a minute and try again.";
  }
  return "Sign-in failed. Please try again.";
}

/** The error an OAuth redirect carries, if any (query string first, then the hash). */
export function redirectError(search: string, hash: string): AuthFailure | null {
  for (const raw of [search, hash.replace(/^#/, "")]) {
    const p = new URLSearchParams(raw);
    const error = p.get("error");
    const description = p.get("error_description");
    if (error || description) {
      const code = p.get("error_code");
      const status = Number(p.get("status") ?? p.get("http_code") ?? NaN);
      return { message: description ?? error, code, status: Number.isFinite(status) ? status : null };
    }
  }
  return null;
}

/** Only same-site paths are followed after sign-in (no open redirect). */
export function safeNext(next: string | null, fallback = "/my-feed"): string {
  return next && /^\/(?!\/)[^\s\\]*$/.test(next) ? next : fallback;
}
