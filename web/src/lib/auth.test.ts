import { describe, expect, it } from "vitest";

import { homeRedirectScript, passwordLoginEnabled } from "./auth-config";
import { authErrorMessage, INVITE_ONLY_MESSAGE, redirectError, safeNext } from "./auth-errors";
import { parseStoredSession } from "./session";

describe("passwordLoginEnabled", () => {
  it("needs the flag and a local Supabase URL", () => {
    expect(passwordLoginEnabled("1", "http://127.0.0.1:54321")).toBe(true);
    expect(passwordLoginEnabled("1", "http://localhost:54321/")).toBe(true);
    expect(passwordLoginEnabled(undefined, "http://127.0.0.1:54321")).toBe(false);
    expect(passwordLoginEnabled("true", "http://127.0.0.1:54321")).toBe(false);
    expect(passwordLoginEnabled("1", "https://abcd.supabase.co")).toBe(false);
    expect(passwordLoginEnabled("1", "http://127.0.0.1.evil.example")).toBe(false);
    expect(passwordLoginEnabled("1", undefined)).toBe(false);
  });
});

describe("auth errors", () => {
  it("maps the invite hook rejection to the invite-only message", () => {
    expect(authErrorMessage({ message: "Sign-up is by invitation only.", status: 403 })).toBe(INVITE_ONLY_MESSAGE);
    expect(authErrorMessage({ message: "Invalid login credentials", status: 400, code: "invalid_credentials" })).toBe(
      "Wrong email or password.",
    );
    expect(authErrorMessage({ message: "boom", status: 500 })).toMatch(/try again/);
  });

  it("reads errors from an OAuth redirect (query or hash)", () => {
    const q = redirectError("?error=server_error&error_description=Sign-up+is+by+invitation+only.", "");
    expect(q).toMatchObject({ message: "Sign-up is by invitation only." });
    expect(authErrorMessage(q!)).toBe(INVITE_ONLY_MESSAGE);
    expect(redirectError("", "#error=access_denied&error_code=403")).toMatchObject({
      message: "access_denied",
      code: "403",
    });
    expect(redirectError("?code=abc", "")).toBeNull();
  });

  it("follows only same-site paths after sign-in", () => {
    expect(safeNext("/bookmarks")).toBe("/bookmarks");
    expect(safeNext("/items/abc?x=1")).toBe("/items/abc?x=1");
    expect(safeNext(null)).toBe("/my-feed");
    expect(safeNext("https://evil.example")).toBe("/my-feed");
    expect(safeNext("//evil.example")).toBe("/my-feed");
    expect(safeNext("/\\evil.example")).toBe("/my-feed");
  });
});

describe("parseStoredSession", () => {
  const session = {
    access_token: "jwt",
    refresh_token: "r",
    expires_at: 1_900_000_000,
    user: { id: "u1", email: "a@b.c", user_metadata: { user_name: "octo", avatar_url: "https://x/a.png" } },
  };

  it("reads the user and token from supabase-js's stored session", () => {
    expect(parseStoredSession(JSON.stringify(session))).toEqual({
      user: { id: "u1", email: "a@b.c", name: "octo", avatarUrl: "https://x/a.png" },
      accessToken: "jwt",
      expiresAt: 1_900_000_000,
    });
  });

  it("falls back to the email name and rejects junk", () => {
    const plain = { ...session, user: { id: "u1", email: "reader@b.c" } };
    expect(parseStoredSession(JSON.stringify(plain))?.user.name).toBe("reader");
    expect(parseStoredSession(null)).toBeNull();
    expect(parseStoredSession("not json")).toBeNull();
    expect(parseStoredSession(JSON.stringify({ user: { id: "u1" } }))).toBeNull();
  });
});

describe("homeRedirectScript", () => {
  it("is a self-contained, guarded statement", () => {
    expect(homeRedirectScript).toMatch(/^try\{.*\}catch\(e\)\{\}$/);
    expect(homeRedirectScript).toContain('location.replace("/my-feed")');
  });
});
