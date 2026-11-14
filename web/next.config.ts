import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Stop `next dev` from writing web/AGENTS.md when it detects a coding agent.
  agentRules: false,
  cacheComponents: true,
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
