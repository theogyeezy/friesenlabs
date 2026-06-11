# LANE MINI — MVP: demo tenant golden path + knowledge corpus seeding

You are a FLEETAGENT worker lane on the repo in the CURRENT DIRECTORY (~/dev/friesenlabs, multi-tenant agentic CRM "Uplift"). Read CLAUDE.md, CONTRIBUTING.md, AGENTS.md first.

## Git contract (non-negotiable)
- `git fetch origin` then `git switch -c feat/mvp-demo-knowledge origin/main`
- Territory: `scripts/` (NEW files only — never modify existing scripts), `tests/`, and NEW fixture/data files under a new `scripts/demo/` and `agents/knowledge_seed/` (new dirs are yours). Do NOT edit api/, web/, infra/, db/schema.sql, conv/, living docs (CLAUDE.md/README/TODO/BUILD_STATUS).
- Small conventional commits; rebase on origin/main before push; push ONLY your branch; `gh pr create --fill`; fix CI red on your branch; do NOT merge; do NOT push main. This is an MVP branch — do NOT delete it at any point.
- DoD: `pytest -q` green locally for everything you add.

## Tasks
1. **Demo tenant golden path.** A demo-dataset generator already exists in the repo (find it — it was merged recently as feat/matt-demo-dataset-generator). What's missing per the ratified Option B decision (docs/decisions/): the COMMITTED fixture and the loader. Produce: (a) a deterministic committed fixture (seeded RNG) at `scripts/demo/fixture/` — a coherent "hero arc" small business (companies, contacts, deals across stages with believable values, activities) sized ~50 companies/200 contacts/80 deals; (b) `scripts/demo/load_demo_tenant.py` — idempotent loader that, given DB env + a tenant id, loads the fixture through the SAME tenant-scoped patterns the codebase uses (SET LOCAL app.current_tenant as the app role; never as table owner; re-running must not duplicate). (c) tests: fixture validates against the schema; loader is idempotent (use the repo's existing DB test harness patterns from tests/integration/).
2. **Knowledge corpus seeding.** Chat citations need a tenant knowledge corpus (TODO mentions seed-knowledge-corpus). Build `agents/knowledge_seed/` with: (a) ~25 short markdown knowledge docs for the demo tenant (product FAQ, pricing policy, sales playbook snippets, onboarding guide — write believable generic-CRM content, no real names); (b) `scripts/demo/seed_knowledge.py` that chunks + embeds + upserts them via the repo's EXISTING rag/embedding client interfaces (find PgRagClient / the ingest embed path and reuse it; do not invent a new pipeline). Idempotent. (c) a test that the seeded corpus round-trips through the existing rag search interface (skip-if-no-DB pattern the repo already uses).

Work autonomously until the PR is green. Final message: PR number + branch + summary + anything blocked.
