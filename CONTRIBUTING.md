# Contributing — branching & release (Phase 12)

## Trunk-based development
Small, fast team on a monorepo → **trunk-based development** with short-lived branches and environment
promotion by pipeline. Not long-lived environment branches (GitFlow is overkill for a 2-3 person team).
One trunk, always deployable; environments are **deploys of the trunk, not branches**.

> Note: this repo's trunk is **`prod`** (the default branch). CI runs on push/PR to `prod`.

| Branch | Purpose / rules |
|---|---|
| `prod` | The trunk. Always green + deployable. Protected: no direct pushes; merge only via PR with passing CI + 1 review. |
| `feat/<area>-<slug>` | Short-lived feature branch off trunk (e.g. `feat/agents-greenlight-queue`). Hours-to-days, not weeks. Squash-merge, then delete. |
| `fix/<slug>` · `chore/<slug>` | Bug fixes / maintenance, same lifecycle. |
| `hotfix/<slug>` | Urgent prod fix off trunk; fast-tracked review, then tagged + promoted immediately. |

## Environments = deploys, not branches
`dev` / `staging` / `prod` are deploys of the same trunk via Terraform, parameterized by
`infra/envs/{dev,staging,prod}.tfvars`. Secrets live in Secrets Manager (referenced by ARN), never in
state or tfvars.

## CI gate (`.github/workflows/ci.yml`)
Every PR to `prod` must pass:
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
