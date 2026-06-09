import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite + React 18 (pinned to match the prototype). The prototype shared state
// through window globals; we keep that runtime registry alive (see src/globals.ts)
// while wiring real ES module imports so init order is deterministic.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  preview: { port: 4173 },
  build: {
    outDir: "dist",
    // The converted prototype leans on a few intentional patterns the minifier
    // does not need to police; keep the build lenient so it stays green.
    chunkSizeWarningLimit: 4000,
  },
});
