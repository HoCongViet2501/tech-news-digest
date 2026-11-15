import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Stop `next dev` from writing web/AGENTS.md when it detects a coding agent.
  agentRules: false,
  cacheComponents: true,
  cacheLife: {
    // Public pages and their data (docs/PLAN.md §16 W3 step 3): regenerated in the
    // background at most every 15 minutes; ingest runs every 3 hours anyway.
    feed: {
      stale: 300,
      revalidate: 900,
      expire: 86_400,
    },
  },
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
