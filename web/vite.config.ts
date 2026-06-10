import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "fs";

// Emits the social-share card (web/og.png) into the build output as /og.png so
// it survives publicDir:false in real builds (referenced by og:image in index.html).
const emitOgImage = {
  name: "emit-og-image",
  generateBundle() {
    this.emitFile({ type: "asset", fileName: "og.png", source: fs.readFileSync("og.png") });
  },
};

// Vite + React 18 (pinned to match the prototype). The prototype shared state
// through window globals; we keep that runtime registry alive (see src/globals.ts)
// while wiring real ES module imports so init order is deterministic.
//
// REAL builds (VITE_API_MOCK=0/false in the build env — npm run build:real /
// build:auth) drop the public/ directory: it holds only the founder demo
// photos (matt-yee.jpeg, nick-friesen.png), which are mock-build assets and
// must not ship in a production dist. The landing page renders initials
// avatars in real builds (src/screens/landing.tsx gates the photo paths the
// same way). If a real production asset ever needs public/, move the demo
// photos behind gated imports instead of re-enabling this wholesale.
const realBuild =
  process.env.VITE_API_MOCK === "0" || process.env.VITE_API_MOCK === "false";

export default defineConfig({
  plugins: [react(), emitOgImage],
  publicDir: realBuild ? false : "public",
  server: { port: 5173 },
  preview: { port: 4173 },
  build: {
    outDir: "dist",
    // The converted prototype leans on a few intentional patterns the minifier
    // does not need to police; keep the build lenient so it stays green.
    chunkSizeWarningLimit: 4000,
  },
});
