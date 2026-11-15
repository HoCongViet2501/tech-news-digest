import { expect, test, type Page } from "@playwright/test";

import { ITEMS, itemId, SOURCES, TAGS } from "./fixtures";

// Covers docs/PLAN.md §16 W3 acceptance: browse Popular, a tag page and search.
// Fixtures come from e2e/fixtures.ts (seeded in global setup).

async function blockThirdParty(page: Page) {
  // Image proxy and favicons: not needed for these checks, and tests stay offline.
  await page.route(/^https:\/\/(wsrv\.nl|www\.google\.com)\//, (route) => route.abort());
}

test.beforeEach(async ({ page }) => blockThirdParty(page));

test("Popular shows ranked cards with sources, tags and also-on", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Popular" })).toBeAttached();

  const card = page.getByRole("article").filter({ hasText: ITEMS.vietnamese.title });
  await expect(card).toBeVisible();
  await expect(card).toContainText(SOURCES.hn.name);
  await expect(card).toContainText(`also on ${SOURCES.lobsters.name}`);
  await expect(card.getByRole("link", { name: `#${TAGS.child.slug}` })).toBeVisible();

  // A cover that fails to load is hidden, leaving the favicon-on-gradient fallback.
  const special = page.getByRole("article").filter({ hasText: ITEMS.special.title });
  await expect(special.locator('img[src*="wsrv.nl"]')).toBeHidden();
  await expect(special.locator('img[src*="favicons"]')).toBeAttached();

  // Special characters are rendered as text, not markup.
  await expect(page.getByRole("link", { name: ITEMS.special.title, exact: true })).toBeVisible();

  // The near-duplicate mirror is folded into the cluster's card.
  await expect(page.getByText(ITEMS.sibling.title)).toHaveCount(0);
});

test("sort tabs switch between Popular, Most upvoted and Discussed", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "Sort" }).getByRole("link", { name: "Discussed" }).click();
  await expect(page).toHaveURL(/\/discussed$/);
  await expect(page.getByRole("heading", { level: 1, name: "Discussed" })).toBeAttached();
  await expect(page.getByRole("article").filter({ hasText: ITEMS.vietnamese.title })).toBeVisible();
});

test("infinite scroll loads the next page from Supabase", async ({ page }) => {
  await page.goto("/");
  const cards = page.getByRole("article");
  await expect(cards).toHaveCount(24);
  const rpc = page.waitForRequest((r) => r.url().includes("/rest/v1/rpc/popular_feed"));
  await page.keyboard.press("End");
  expect((await rpc).postDataJSON()).toMatchObject({ p_sort: "popular", p_offset: 24 });
  await expect.poll(() => cards.count()).toBeGreaterThan(24);
});

test("tag page shows only items with the tag or its sub-tags", async ({ page }) => {
  await page.goto("/tags");
  await page.getByRole("link", { name: new RegExp(`^#${TAGS.topic.slug}\\b`) }).first().click();
  await expect(page).toHaveURL(new RegExp(`/tags/${TAGS.topic.slug}$`));
  await expect(page.getByRole("heading", { level: 1, name: `#${TAGS.topic.slug}` })).toBeVisible();

  const cards = page.getByRole("article");
  await expect(cards).toHaveCount(2);
  await expect(cards.filter({ hasText: ITEMS.vietnamese.title })).toBeVisible(); // via #e2e-child
  await expect(cards.filter({ hasText: ITEMS.special.title })).toBeVisible();
  await expect(page.getByText(ITEMS.untagged.title)).toHaveCount(0);
});

test("item page links to the original and the discussions", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: ITEMS.vietnamese.title }).click();
  await expect(page).toHaveURL(`/items/${itemId(ITEMS.vietnamese.key)}`);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(ITEMS.vietnamese.title);
  // The previous page stays mounted (hidden) for back navigation, hence the visible filter.
  await expect(page.getByText(ITEMS.vietnamese.description).filter({ visible: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /Read the original/ })).toHaveAttribute(
    "href",
    "https://e2e.invalid/article/vietnamese",
  );
  await expect(page.getByRole("link", { name: new RegExp(`Discuss on ${SOURCES.lobsters.name}`) })).toHaveAttribute(
    "href",
    `${SOURCES.lobsters.url}/item/vietnamese`,
  );
  await expect(page.getByRole("link", { name: ITEMS.sibling.title })).toBeVisible();
});

test('search "kien truc" finds "kiến trúc"', async ({ page }) => {
  await page.goto("/search");
  const box = page.getByRole("main").getByRole("searchbox", { name: "Search" });
  await box.fill("kien truc");
  await box.press("Enter");
  await expect(page).toHaveURL(/\/search\?q=kien\+truc$/);
  await expect(page.getByRole("article").filter({ hasText: ITEMS.vietnamese.title })).toBeVisible();
});

test("search tolerates typos in titles", async ({ page }) => {
  await page.goto("/search?q=webassembli");
  await expect(page.getByRole("article").filter({ hasText: ITEMS.special.title })).toBeVisible();
});
