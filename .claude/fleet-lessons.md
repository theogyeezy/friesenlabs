# Fleet lessons — friesenlabs repo

Repo-specific lessons for the /fleet skill (conventions, territory couplings, the verify command
that actually works here). One dated line each. Committed so every lane learns from every lane.

2026-06-11 · verify command: `pytest -q` (full) or targeted `pytest <path> -x`; web lane uses `cd web && npm run typecheck && npm run build` + Playwright. CI gate = python (real Postgres+pgvector isolation) · terraform fmt/validate · web · smoke.
2026-06-11 · DB stores open lazy pools now (minconn=1); CI Postgres has limited connection slots — integration tests that each spin a store can still exhaust them under heavy fan-out. Keep DB-touching tasks per-wave modest.
2026-06-11 · `shared/` is BOTH a repo package AND the prefix used in decision-brief `## Sources` citations (provenance to the claude-api skill). Backticked `shared/*.md` mentions in docs/decisions are citations, not repo paths — do NOT "fix" them.
2026-06-11 · territories that conflict at merge: db/schema.sql (EOF-append only, keep all hunks), api/asgi.py + api/app.py (route includes — keep all), db/roles.sql (grants — keep all). RLS-EXEMPT tables (leads/support_requests/workspace_keys) need explicit roles.sql grants — the tenant-table gate won't catch a missing one.
2026-06-11 · customer-readiness modules mostly already have INTEGRATION tests; the genuine unit-coverage gaps were the integration-only ones (billing_routes, support_routes, limits, leads). Scout existing tests/ first and decompose only real gaps — don't re-cover what's covered.
