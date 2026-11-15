import { test } from "@playwright/test";

// Not a check: captures pages for review. Runs only with E2E_SCREENSHOTS=<dir>.
const dir = process.env.E2E_SCREENSHOTS;

test.skip(!dir, "set E2E_SCREENSHOTS=<dir> to capture screenshots");

const shots = [
  { name: "popular-desktop-light", path: "/", viewport: { width: 1366, height: 900 }, scheme: "light" },
  { name: "popular-mobile-dark", path: "/", viewport: { width: 390, height: 844 }, scheme: "dark" },
  { name: "item-mobile-light", path: "/items/ITEM", viewport: { width: 390, height: 844 }, scheme: "light" },
  { name: "tag-desktop-dark", path: "/tags/e2e-topic", viewport: { width: 1366, height: 900 }, scheme: "dark" },
] as const;

for (const s of shots) {
  test(s.name, async ({ browser }) => {
    const context = await browser.newContext({ viewport: s.viewport, colorScheme: s.scheme, deviceScaleFactor: 2 });
    const page = await context.newPage();
    const { itemId, ITEMS } = await import("./fixtures");
    await page.goto(s.path.replace("ITEM", itemId(ITEMS.vietnamese.key)));
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${dir}/${s.name}.png` });
    await context.close();
  });
}

// Signed-in pages, as the dedicated `viewer` fixture user (the account specs use `reader`).
test.describe("signed in", () => {
  test.describe.configure({ mode: "serial" });

  test("onboarding, my-feed, card-actions", async ({ browser }) => {
    const { ITEMS, USERS, resetUser } = await import("./fixtures");
    await resetUser(USERS.viewer.email);
    const context = await browser.newContext({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 2 });
    const page = await context.newPage();
    await page.goto("/sign-in");
    await page.getByLabel("Email").fill(USERS.viewer.email);
    await page.getByLabel("Password").fill(USERS.viewer.password);
    await page.getByRole("button", { name: "Sign in with password" }).click();
    await page.waitForURL(/\/onboarding$/);
    for (const name of ["E2E Topic", "E2E Child", "E2E Extra"]) {
      await page.getByRole("button", { name, exact: true }).click();
    }
    await page.getByLabel(/Anything else/).fill("Backend in Go and Postgres, event sourcing with Kafka");
    await page.screenshot({ path: `${dir}/onboarding-desktop-light.png` });

    await page.getByRole("button", { name: "Show my feed" }).click();
    await page.waitForURL(/\/my-feed$/);
    const card = page.getByRole("article").filter({ hasText: ITEMS.vietnamese.title });
    await card.getByRole("button", { name: "Upvote" }).click();
    await card.getByRole("button", { name: "Bookmark" }).click();
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${dir}/my-feed-desktop-light.png` });

    await card.getByRole("button", { name: "More actions" }).click();
    await card.screenshot({ path: `${dir}/card-actions-menu.png` });
    await page.keyboard.press("Escape");

    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.goto("/my-feed");
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${dir}/my-feed-mobile-dark.png` });

    await page.goto(`/items/${(await import("./fixtures")).itemId(ITEMS.vietnamese.key)}`);
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: `${dir}/item-actions-mobile-dark.png` });

    await page.goto("/settings");
    await page.getByRole("button", { name: "Delete my history" }).click();
    await page.screenshot({ path: `${dir}/settings-mobile-dark.png`, fullPage: true });
    await context.close();
  });
});
