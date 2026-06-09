# Contributing — branching & release (Phase 12)

## Trunk-based development
Small, fast team on a monorepo → **trunk-based development** with short-lived branches and environment
promotion by pipeline. Not long-lived environment branches (GitFlow is overkill for a 2-3 person team).
One trunk, always deployable; environments are **deploys of the trunk, not branches**.

> Note: this repo's trunk is **`main`** (the default branch). CI runs on push/PR to `main`.

| Branch | Purpose / rules |
|---|---|
| `main` | The trunk. Always green + deployable. Protected: no direct pushes; merge only via PR with passing CI + 1 review. |
| `feat/<area>-<slug>` | Short-lived feature branch off trunk (e.g. `feat/agents-greenlight-queue`). Hours-to-days, not weeks. Squash-merge, then delete. |
| `fix/<slug>` · `chore/<slug>` | Bug fixes / maintenance, same lifecycle. |
| `hotfix/<slug>` | Urgent prod fix off trunk; fast-tracked review, then tagged + promoted immediately. |

## Environments = deploys, not branches
`dev` / `staging` / `prod` are deploys of the same trunk via Terraform, parameterized by
`infra/envs/{dev,staging,prod}.tfvars`. Secrets live in Secrets Manager (referenced by ARN), never in
state or tfvars.

## CI gate (`.github/workflows/ci.yml`)
Every PR to `main` must pass:
- **python** — `pytest` (unit + integration) + `scripts/isolation_test.py` (the multi-tenant gate).
- **terraform** — `fmt -check` + `validate` over `infra/`.
- **web** — `npm run typecheck` + `npm run build` + Playwright e2e.

The isolation test is the one you cannot skip.

## Local pre-flight
```bash
pytest -q && bash scripts/smoke_all.sh && python scripts/isolation_test.py
(cd infra && terraform fmt -check -recursive && terraform validate)
(cd web && npm run build && npm run typecheck && npx playwright test)
```

## Two-lane contract (2026-06-09)

Two people run agent loops in parallel on separate machines. File ownership is the conflict guard
— each lane only ever edits its own territory.

| Territory | Owner |
|---|---|
| `infra/**`, `.github/workflows/**`, `db/roles.sql`, all Dockerfiles, existing `scripts/*`, terraform plan/apply, AWS mutations, secret population | **LANE NICK** |
| `api/`, `agents/`, `conv/`, `signup/`, `worker/`, `ml/`, `ingest/`, `web/`, `tests/`, `semantic/*.js` + `semantic/model/` + `semantic/test/`, `shared/*.py` + `shared/schemas/`, `requirements*.txt` | **LANE MATT** |
| `db/schema.sql` | Matt appends only (new tables at EOF + adding names to the `tenant_tables` array; RLS-exempt pre-tenant tables carry an `-- RLS-EXEMPT: <reason>` comment). Nick applies it live and runs the isolation test against that exact sha. |
| `CLAUDE.md`, `README.md`, `shared/COST.md` | Nick single-writer. Matt records doc-worthy changes in his PR description; Nick folds them in. |
| `TODO.md` | Each lane checks off only items in its own sections; never reflow or renumber the other lane's lines; rebase on `origin/main` immediately before any commit touching it. |
| `BUILD_STATUS.md` | Two delimited lane-log sections; write only your own. |
| New files under `scripts/` | Matt may CREATE new scripts (e.g. verify scripts); never modify existing ones. |

**The handoff: `infra/REQUESTS.md`.** Matt never edits `infra/**`. Any infra need (new secret,
task-def env wiring, IAM policy, CI job, Lambda resource) is appended there as a fenced HCL/SQL/spec
block — exact resources, variables with safe `""` defaults, outputs. Nick implements, validates,
and applies them serially, in order.

**Env-var / secret-name contract.** `shared/config.py` (Matt-owned) is the single source of truth
for every env var and Secrets Manager path the app reads. New names land there first; Nick mirrors
names from it into task defs and never invents names in infra. Frozen worker contract:
`UPLIFT_ENV_ID`, `UPLIFT_ENV_KEY`, `CLOUDWATCH_METRICS`, `CUBE_ENDPOINT`, `AWS_REGION`,
`DB_USER`/`DB_PASS`/`DB_HOST`/`DB_NAME`.

**Ordered cross-lane sequences (never parallel):**
1. schema append (Matt) → live apply via `api.migrate` (Nick) → `isolation_test.py` live (Nick)
2. `signup/lambda_handler.py` + adapters merge (Matt) → provisioning Lambda deploy + SFN ARN pin (Nick)
3. pinned/hashed requirements lock (Matt) → `--require-hashes` Dockerfiles (Nick)
4. rotation-tolerant refresh-token client (Matt) → shorter `refresh_token_validity` (Nick)

**Merge discipline (this sprint).** `main` is currently unprotected; lane PRs squash-merge on green
CI (`gh pr merge --squash`), cross-review is post-hoc. Nick is the merge serializer when two PRs
touch living docs. Each loop cuts its own `feat/nick-*` / `feat/matt-*` branches; branches are never
shared across lanes.
