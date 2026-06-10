/// <reference types="vite/client" />

// Vite client types so modules can reference import.meta.env.VITE_* directly.
// IMPORTANT (bundle hygiene): build-time gates MUST use the full literal chain
// `import.meta.env.VITE_API_MOCK` — that exact member expression is what Vite
// statically replaces at build time, which is what lets rollup fold the
// condition and drop mock-only branches/chunks from real-mode bundles. An
// indirection like `const env = import.meta.env; env.VITE_API_MOCK` defeats
// the static replacement and ships the mock code to production.
