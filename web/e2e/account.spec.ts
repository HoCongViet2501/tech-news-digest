import { expect, test, type Page } from "@playwright/test";

import { ITEMS, resetUser, USERS } from "./fixtures";

// docs/PLAN.md §16 W4 acceptance: sign-in, onboarding, hide and bookmark. Uses the local
// email/password sign-in (src/lib/auth-config.ts), so no GitHub account is involved.
// The tests share one invited reader and build on each other, so they run in order; the
// reader's state is reset first (also on a retry).

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => resetUser(USERS.reader.email));

test.beforeEach(async ({ page }) => {
  // Image proxy and favicons: not needed here, and tests stay offline.
  await page.route(/^https:\/\/(wsrv\.nl|www\.google\.com)\//, (route) => route.abort());
});

async function passwordForm(page: Page, who: { email: string; password: string }) {
  await page.goto("/sign-in");
  await page.getByLabel("Email").fill(who.email);
  await page.getByLabel("Password").fill(who.password);
}

async function signIn(page: Page) {
  await passwordForm(page, USERS.reader);
  await page.getByRole("button", { name: "Sign in with password" }).click();
  await expect(page).toHaveURL(/\/(my-feed|onboarding)$/);
}

const card = (page: Page, title: string) => page.getByRole("article").filter({ hasText: title });

test("a non-invited account is rejected with an invite-only message", async ({ page }) => {
  await passwordForm(page, USERS.stranger);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("invite-only");
  await expect(page).toHaveURL(/\/sign-in$/);
});

test("an OAuth redirect rejected by the invite hook shows the same message", async ({ page }) => {
  await page.goto("/auth/callback?error=server_error&error_description=Sign-up+is+by+invitation+only.");
  await expect(page.getByRole("main").getByRole("alert")).toContainText("invite-only");
});

test("invited reader signs in, onboards, and My Feed shows the followed tags", async ({ page }) => {
  await signIn(page);
  // First sign-in: no followed tags yet, so My Feed sends the reader to onboarding.
  await expect(page).toHaveURL(/\/onboarding$/);
  const submit = page.getByRole("button", { name: "Show my feed" });
  await expect(submit).toBeDisabled();
  for (const name of ["E2E Topic", "E2E Child", "E2E Extra"]) {
    await page.getByRole("button", { name, exact: true }).click();
  }
  await page.getByLabel(/Anything else/).fill("Backend, event sourcing and Kafka");
  await submit.click();

  await expect(page).toHaveURL(/\/my-feed$/);
  await expect(page.getByRole("heading", { level: 1, name: "My Feed" })).toBeVisible();
  for (const item of [ITEMS.vietnamese, ITEMS.special, ITEMS.extra]) {
    await expect(card(page, item.title)).toBeVisible();
  }

  // The session survives a reload, and "/" is My Feed for a signed-in reader.
  await page.goto("/");
  await expect(page).toHaveURL(/\/my-feed$/);
  await expect(page.getByRole("banner").getByRole("link", { name: "My Feed" })).toBeVisible();
});

test("hiding an item removes it, and it stays gone after a reload", async ({ page }) => {
  await signIn(page);
  await page.goto("/my-feed");
  const target = card(page, ITEMS.special.title);
  await expect(target).toBeVisible();

  const saved = page.waitForResponse((r) => r.url().includes("/rest/v1/rpc/item_action") && r.ok());
  await target.getByRole("button", { name: "Hide" }).click();
  await expect(page.getByText(ITEMS.special.title)).toHaveCount(0);
  await expect(page.getByRole("status").filter({ hasText: "Hidden." })).toBeVisible();
  await saved;

  await page.reload();
  await expect(card(page, ITEMS.vietnamese.title)).toBeVisible();
  await expect(page.getByText(ITEMS.special.title)).toHaveCount(0);
});

test("a bookmark appears in /bookmarks", async ({ page }) => {
  await signIn(page);
  await page.goto("/my-feed");
  const target = card(page, ITEMS.vietnamese.title);
  const bookmark = target.getByRole("button", { name: "Bookmark" });
  const saved = page.waitForResponse((r) => r.url().includes("/rest/v1/rpc/item_action") && r.ok());
  await bookmark.click();
  await expect(target.getByRole("button", { name: "Bookmarked" })).toHaveAttribute("aria-pressed", "true");
  await saved;

  await page.goto("/bookmarks");
  await expect(page.getByRole("heading", { level: 1, name: "Bookmarks" })).toBeVisible();
  await expect(card(page, ITEMS.vietnamese.title)).toBeVisible();
  // Reactions are loaded for every card, so the state shows here too.
  await expect(card(page, ITEMS.vietnamese.title).getByRole("button", { name: "Bookmarked" })).toBeVisible();
});

test("opening an item records a click, which shows in the reading history", async ({ page }) => {
  await signIn(page);
  await page.goto("/my-feed");
  await card(page, ITEMS.extra.title).getByRole("link", { name: ITEMS.extra.title }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(ITEMS.extra.title);

  // Events are batched and flushed when the page is hidden or unloaded.
  const sent = page.waitForRequest((r) => r.url().includes("/rest/v1/rpc/record_events"));
  await page.evaluate(() => window.dispatchEvent(new Event("pagehide")));
  const request = await sent;
  expect(request.headers()["authorization"]).toMatch(/^Bearer /);
  expect(request.postDataJSON()).toEqual({ p_events: [{ item_id: expect.any(String), kind: "click" }] });
  expect((await request.response())?.ok()).toBe(true);

  await page.goto("/history");
  await expect(card(page, ITEMS.extra.title)).toBeVisible();
});

test("signing out returns to the public site", async ({ page }) => {
  await signIn(page);
  await page.getByRole("button", { name: /Account menu/ }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page.getByRole("banner").getByRole("link", { name: "Sign in" })).toBeVisible();
  await page.goto("/my-feed");
  await expect(page.getByRole("link", { name: "Sign in" }).last()).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(0);
});
