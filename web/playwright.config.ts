import { defineConfig, devices } from "@playwright/test";

const port = 3000;
const isCI = Boolean(process.env.CI);

// Locally, NEXT_PUBLIC_SUPABASE_* come from .env.local (Next reads it too); CI sets them
// from `supabase status`. The API tests below need them in this process as well.
try {
  process.loadEnvFile(".env.local");
} catch {
  // No .env.local (CI).
}

export default defineConfig({
  testDir: "./e2e",
  forbidOnly: isCI,
  retries: isCI ? 1 : 0,
  reporter: isCI ? [["github"], ["html", { open: "never" }]] : "list",
  // Seeds fixture rows into the local Supabase (idempotent, never truncates) and removes
  // them afterwards. Runs after the web server has started.
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  use: {
    baseURL: `http://localhost:${port}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // CI runs `pnpm e2e:seed && pnpm build` first and serves the production build;
    // E2E_START=1 does the same locally. `next` is on PATH when run through `pnpm e2e`.
    command: isCI || process.env.E2E_START ? `next start --port ${port}` : `next dev --port ${port}`,
    url: `http://localhost:${port}`,
    reuseExistingServer: !isCI,
    timeout: 120_000,
  },
});
