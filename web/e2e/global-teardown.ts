import { cleanup } from "./fixtures";

/** Leaves the shared local database as it was; E2E_KEEP_FIXTURES=1 keeps them for debugging. */
export default async function globalTeardown() {
  if (!process.env.E2E_KEEP_FIXTURES) await cleanup();
}
