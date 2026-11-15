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
