import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The component/integration layer of the testing trophy: real React components rendered in a
// simulated DOM (jsdom) via @testing-library/react, fired with real events. Fast + deterministic,
// runs in CI with no browser. It does NOT render real pixels/layout — that's what the Playwright
// e2e + visual layers cover (see TESTING.md). Co-located *.test.tsx / __tests__ files only;
// e2e/ (Playwright) is excluded so the two runners never collide.
export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["e2e/**", "node_modules/**", "dist/**", "dist-*/**", "test/*.test.mjs"],
    css: false,
    coverage: {
      provider: "v8",
      reportsDirectory: "./coverage",
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.{test,spec}.{ts,tsx}",
        "src/**/__tests__/**",
        "src/main.tsx",
        "src/**/*.d.ts",
      ],
      reporter: ["text-summary", "html"],
    },
  },
});
