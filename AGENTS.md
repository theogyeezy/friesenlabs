# AGENTS.md — contract for non-Claude agents (Codex et al.)

Read `CLAUDE.md` (orientation + hard constraints) and `CONTRIBUTING.md` (branching,
lane ownership, CI gate) first. They are the source of truth; this file is the
quick contract + the commands that define "done".

## Definition of done
A change is done only when the applicable checks pass locally:

```bash
pytest -q                                   # unit + integration (real Postgres tests skip w/o DB)
python scripts/isolation_test.py            # multi-tenant gate — NEVER skip after data/agent/auth changes
(cd infra && terraform fmt -check -recursive && terraform validate)
(cd web && npm run typecheck && npm run build && npx playwright test)
```

Run the subset your diff touches; CI runs all of it on the PR and must be green
before merge.

## Git contract (one branch = one writer, ever)
- Claim = a GitHub issue. Work only that issue's files.
- `git fetch && git switch -c feat/codex-<slug> origin/main` — always cut from
  fresh `origin/main`, never from another agent's branch.
- Small diffs, hours-not-days. Rebase on `origin/main` immediately before push.
- Push only your own `feat/codex-*` / `fix/codex-*` branch. Never push `main`.
  Never touch another lane's branch.
- Open a PR (`gh pr create --fill`) referencing the issue; conventional-commit
  title (it becomes the squash commit). Fix CI red on your own branch.
- The boss agent merges serially on green CI. Do not merge your own PR.

## Hard never-dos (full list + rationale in CLAUDE.md)
- No live cloud mutation (`terraform apply`, AWS/Anthropic resource creation) —
  author + `terraform validate` only; mark such steps `BLOCKED: Lane Nick`.
- No real sends (email/SMS/CRM writes) — Greenlight-gated, draft-only.
- No secrets in the repo (Secrets Manager / env refs only); `docs/*.pdf` and
  `docs/*.txt` are confidential and gitignored.
- Tenant identity comes ONLY from the verified Cognito JWT claim (THE TRUST RULE).
- Living docs have single writers (see `CONTRIBUTING.md` §Two-lane contract);
  record doc-worthy changes in your PR description instead of editing them.
