import { defineConfig } from "vitest/config";

export default defineConfig({
  // The "@/..." alias from tsconfig.json.
  resolve: { tsconfigPaths: true },
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
