import { defineConfig, devices } from "@playwright/test";

// Headless e2e against production preview builds. Two bundles are built and
// served (the webServer entries below build them; reuseExistingServer makes
// local re-runs cheap):
//
//   :4173  MOCK build (VITE_API_MOCK unset => mock). The prototype + demo
//          surfaces; every spec except realmode.spec.ts / integrations.spec.ts
//          / auth.spec.ts runs here.
//   :4174  REAL build (VITE_API_MOCK=0 baked at BUILD time). Exactly what a
//          production deploy ships — there is no runtime URL seam to flip
//          modes (the old `?apimock=0` param was removed as a prod honesty
//          fix). realmode.spec.ts + integrations.spec.ts + pipeline.spec.ts +
//          contacts.spec.ts + agents.spec.ts + signup-real.spec.ts (the Stripe
//          checkout redirect + resume/poll) run here fully offline, stubbing
//          the API with page.route. Cognito env vars are absent, so auth stays
//          inert and the sign-in gate is open.
//   :4175  AUTH build (VITE_API_MOCK=0 + Cognito env baked at BUILD time, with
//          a .invalid Hosted UI domain that can never resolve). auth.spec.ts
//          runs here fully offline: the Hosted UI authorize/token endpoints
//          and the API are all stubbed with page.route, exercising the real
//          sign-in gate, the PKCE callback exchange, and the
//          401 -> session-expired -> sign-in path.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: true,
  reporter: [["list"]],
  use: {
    headless: true,
    trace: "off",
  },
  projects: [
    {
      name: "chromium",
      testIgnore: [/realmode\.spec\.ts/, /integrations\.spec\.ts/, /pipeline\.spec\.ts/, /contacts\.spec\.ts/, /agents\.spec\.ts/, /workflows\.spec\.ts/, /reports\.spec\.ts/, /dashboards\.spec\.ts/, /knowledge\.spec\.ts/, /onboarding\.spec\.ts/, /signup-real\.spec\.ts/, /auth\.spec\.ts/, /conversion\.spec\.ts/, /billing\.spec\.ts/, /cortex\.spec\.ts/, /depth-ui\.spec\.ts/, /studio\.spec\.ts/],
      use: { ...devices["Desktop Chrome"], baseURL: "http://localhost:4173" },
    },
    {
      name: "chromium-real",
      testMatch: /(realmode|integrations|pipeline|contacts|agents|workflows|reports|dashboards|knowledge|onboarding|signup-real|billing|cortex|depth-ui|studio)\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: "http://localhost:4174" },
    },
    {
      name: "chromium-auth",
      testMatch: /(auth|conversion)\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: "http://localhost:4175" },
    },
  ],
  webServer: [
    {
      command: "npm run build && npm run preview -- --port 4173",
      url: "http://localhost:4173",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: "npm run build:real && npm run preview:real",
      url: "http://localhost:4174",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: "npm run build:auth && npm run preview:auth",
      url: "http://localhost:4175",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
