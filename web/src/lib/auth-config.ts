// Shared by the browser session code and the inline <head> script (no imports, so it can
// be used from the server layout without pulling in supabase-js).

/** localStorage key of the supabase-js session (explicit, so the head script can find it). */
export const AUTH_STORAGE_KEY = "tnd-auth";

/** Set after a follow/block/onboarding change: the next My Feed load re-ranks. */
export const FEED_STALE_KEY = "tnd-feed-stale";

const LOCAL_SUPABASE = /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/?$/;

/**
 * Email/password sign-in exists only for the e2e suite against a local Supabase. It needs
 * the build-time flag AND a local Supabase URL, so a production build (hosted project, where
 * the Email provider is disabled anyway) never shows it, even if the flag leaks.
 */
export function passwordLoginEnabled(
  flag = process.env.NEXT_PUBLIC_DEV_PASSWORD_LOGIN,
  url = process.env.NEXT_PUBLIC_SUPABASE_URL,
): boolean {
  return flag === "1" && Boolean(url && LOCAL_SUPABASE.test(url));
}

/**
 * Runs in <head> before first paint. My Feed is the home page of signed-in users: a full
 * page load of "/" with a stored session goes to /my-feed. Client-side navigation to "/"
 * (the Popular link) does not re-run it, so Popular stays reachable.
 */
export const homeRedirectScript = `try{if(location.pathname==="/"&&!location.search&&localStorage.getItem(${JSON.stringify(AUTH_STORAGE_KEY)}))location.replace("/my-feed")}catch(e){}`;
