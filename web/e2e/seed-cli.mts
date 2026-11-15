// `pnpm e2e:seed` / `pnpm e2e:clean`. CI seeds before `pnpm build`, because the build
// prerenders the public pages from the database (and caches them for 15 minutes).
import { cleanup, seed } from "./fixtures.ts";

const command = process.argv[2] ?? "seed";
if (command === "clean") {
  await cleanup();
  console.log("Removed e2e fixtures.");
} else {
  await seed();
  console.log("Seeded e2e fixtures.");
}
