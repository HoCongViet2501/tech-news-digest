import { execFileSync } from "node:child_process";

import { defineConfig, devices } from "@playwright/test";

const port = 3000;
const isCI = Boolean(process.env.CI);

// NEXT_PUBLIC_SUPABASE_* are needed here too: by the API specs and the user seeding in
// global setup (this process and its workers), and by the web server it starts.
// Order: exported env (CI sets it from `supabase status`), then .env.local, then the
// running local stack itself. Without the last step a checkout with no .env.local (a
// fresh clone or a git worktree) silently skipped the API specs even with the stack up.
try {
  process.loadEnvFile(".env.local");
} catch {
  // No .env.local.
}
if (!process.env.NEXT_PUBLIC_SUPABASE_URL || !process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY) {
  try {
    const status = JSON.parse(
      execFileSync("supabase", ["status", "-o", "json"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }),
    ) as { API_URL?: string; PUBLISHABLE_KEY?: string; ANON_KEY?: string };
    process.env.NEXT_PUBLIC_SUPABASE_URL ||= status.API_URL;
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ||= status.PUBLISHABLE_KEY ?? status.ANON_KEY;
  } catch {
    // Local stack not running: the DB-backed specs fail or skip with a clear message.
  }
}
// Email/password sign-in for the account specs (local Supabase only, see
// src/lib/auth-config.ts). `next build` must see it too: CI sets it for the whole job.
process.env.NEXT_PUBLIC_DEV_PASSWORD_LOGIN ??= "1";

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
