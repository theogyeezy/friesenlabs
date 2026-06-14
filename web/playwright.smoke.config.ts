import { defineConfig, devices } from "@playwright/test";

// Post-deploy PRODUCTION smoke. Unlike the mock-mode e2e (which builds + serves a bundle and stubs
// the backend), this drives the REAL deployed site against the REAL backend — the same check a
// human does by hand after a deploy, encoded so it is repeatable. No webServer: it hits the live
// URL. Run: PROD_SMOKE_URL=https://www.friesenlabs.com npm run test:smoke
// (the spec self-skips when PROD_SMOKE_URL is unset, so it is safe in any pipeline).
export default defineConfig({
  testDir: "./e2e",
  testMatch: /prod-smoke\.spec\.ts/,
  timeout: 60_000,
  reporter: [["list"]],
  use: {
    headless: true,
    baseURL: process.env.PROD_SMOKE_URL || "https://www.friesenlabs.com",
    ...devices["Desktop Chrome"],
    trace: "on-first-retry",
  },
  retries: 1,
});
