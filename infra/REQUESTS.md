# infra/REQUESTS.md — Lane Matt → Lane Nick infra handoff queue

Lane Matt never edits `infra/**`, `db/roles.sql`, `.github/workflows/**`, or Dockerfiles
(see `CONTRIBUTING.md` § Two-lane contract). Anything the app needs from infra is **appended here**
as a request block; Lane Nick implements, `terraform validate`s, applies, and checks it off — in order.

## Request format

```markdown
### REQ-<NNN>: <one-line summary>
- **Status:** OPEN | IN-PROGRESS (Nick) | DONE @<sha> | REJECTED (<reason>)
- **Requested by:** Lane Matt @<sha or PR#>
- **Needed for:** <TODO item / feature>
- **Env/secret names** (must already exist in shared/config.py): <names or n/a>
- **Spec:**
  ```hcl
  # exact resources / variables (safe "" defaults) / outputs — or fenced SQL for GRANTs
  ```
- **Done when:** <verifiable condition>
```

Rules:
- Append-only for Matt; only Nick edits Status lines.
- Every new terraform variable carries a safe `""`/count-0 default so `validate` and the deploy
  pipeline plan stay green before the value exists.
- New env-var or secret NAMES land in `shared/config.py` first; requests reference them, never invent.
- GRANT requests: fenced SQL here, tests GRANT in fixtures — `db/roles.sql` stays Nick-only.

---

## Queue

### REQ-002: GRANT crm_app DML on the pre-tenant signup tables (`accounts`, `stripe_events`)
- **Status:** IN-PROGRESS (Nick) — authored+merged @6d5a210 (#36), empirically CI-proven (DML yes / DELETE denied / REVOKE beats default-privs / idempotent). Live GRANT pending the cycle-4 one-off `api.migrate` Fargate task with a fresh image (live `e0794bc` bundles the pre-grant roles.sql)
- **Requested by:** Lane Matt @feat/matt-signup-stores (PR "feat(signup): tokens + Aurora-backed account/event/OTP stores")
- **Needed for:** TODO INT/P0 "Replace the in-memory `_AccountStore` with an Aurora-backed, RLS-correct store" + P1 "Persist webhook/provisioning idempotency across restarts" (`signup/store_pg.py` connects as crm_app)
- **Env/secret names** (must already exist in shared/config.py): n/a (GRANT only; the token-signer secret ref `SIGNUP_TOKEN_SECRET` already landed in `shared/config.py` with the same PR — no infra resource needed for it yet)
- **Spec:**
  ```sql
  -- For db/roles.sql (Lane Nick's file). Both tables are RLS-EXEMPT (pre-tenant): rows exist
  -- before a tenant_id is provisioned; access is restricted to crm_app DML via GRANTs, not RLS
  -- (see the table comments in db/schema.sql). No DELETE: accounts are parked/flipped, never
  -- deleted by the app; the stripe_events ledger is append-only by design.
  GRANT SELECT, INSERT, UPDATE ON accounts, stripe_events TO crm_app;
  ```
- **Done when:** connected as crm_app, `INSERT`/`SELECT`/`UPDATE` on `accounts` and `stripe_events` succeed (and the CI schema+roles load stays green). Until then the unit tests mock psycopg2 and integration fixtures GRANT locally.
- **NOTE (review finding, medium):** main's `db/roles.sql` has `ALTER DEFAULT PRIVILEGES … GRANT SELECT, INSERT, UPDATE, DELETE` — the no-DELETE intent above needs an explicit `REVOKE DELETE` or it is silently superseded. Lane Nick to reconcile when implementing.

### REQ-001: AI-plane env wiring — `uplift/env-id` secret, worker task-def env, org Anthropic key to the API task ONLY
- **Status:** DONE @6d5a210 (#36) — env-id secret applied + live-verified (`describe-secret uplift/env-id` OK); worker wiring + asymmetry proven (3-agent verification). GATE NOTE: API-task key injection ships behind `var.api_anthropic_env` (default false) — flip in tfvars only after `uplift/anthropic-api-key` + `uplift/env-id` hold real values, else API task startup fails on the empty secret. Deviation rationale follows. ONE DEVIATION (safety): the API-task ANTHROPIC_API_KEY/UPLIFT_ENV_ID injection is gated behind `var.api_anthropic_env` (default **false**) because valueFrom on an EMPTY secret fails task startup (ResourceInitializationError) — flipping it before the key values exist would take the live API down. Flip in tfvars when the secrets are populated; the rendered task def then matches the spec exactly.
- **Requested by:** Lane Matt @feat/matt-asgi-integration
- **Needed for:** TODO P0s "Wire a real `conversation_factory` (fixes `/chat` 503)" + "Build a real tool executor (replace the noop)" + the worker deploy (`worker/worker.py` now builds its tool clients from env in `run()`)
- **Env/secret names** (must already exist in shared/config.py): `ANTHROPIC_API_KEY`, `UPLIFT_ENV_ID`, `UPLIFT_ENV_KEY`, `CLOUDWATCH_METRICS`, `CUBE_ENDPOINT`, `UPLIFT_DB_URL` / `DB_USER` / `DB_PASS` / `DB_HOST` / `DB_NAME` / `DB_PORT` (all in `shared/config.py`); new Secrets Manager secret name `uplift/env-id` (`Config.env_id_secret`)
- **Spec:**
  ```hcl
  # 1) New Secrets Manager secret holding the Managed Agents self-hosted environment id
  #    (value written by Lane Nick after the live create_environment run; "" until then).
  resource "aws_secretsmanager_secret" "env_id" {
    name = "uplift/env-id"
  }

  # 2) WORKER task definition env wiring (the unapplied worker module):
  #    - UPLIFT_ENV_ID      <- valueFrom aws_secretsmanager_secret.env_id
  #    - UPLIFT_ENV_KEY     <- valueFrom the environment-key secret (worker ONLY — never the org key)
  #    - CLOUDWATCH_METRICS = "1"   (enables the workers_polling heartbeat metric)
  #    - CUBE_ENDPOINT      <- var.cube_endpoint   (variable with safe "" default)
  #    - DB_USER / DB_PASS  <- valueFrom the EXISTING crm_app credentials secret
  #    - DB_HOST / DB_NAME / DB_PORT <- existing Aurora outputs (same values the API task uses)
  variable "cube_endpoint" {
    type    = string
    default = ""
  }

  # 3) API task definition ONLY: inject the org Anthropic key
  #    - ANTHROPIC_API_KEY <- valueFrom the existing uplift/anthropic-api-key secret
  #    - UPLIFT_ENV_ID     <- valueFrom aws_secretsmanager_secret.env_id (single-tenant fallback;
  #                            per-tenant rows in tenant_workspaces take precedence)
  #    The org key must NEVER appear in the worker task definition (the worker holds the
  #    environment key only) — this asymmetry is the security boundary.
  ```
- **Done when:** `terraform validate` green with the new secret + variables (safe `""` defaults); the worker task definition shows UPLIFT_ENV_ID / UPLIFT_ENV_KEY / CLOUDWATCH_METRICS=1 / CUBE_ENDPOINT / DB_* and NO `ANTHROPIC_API_KEY`; the API task definition shows `ANTHROPIC_API_KEY` (from `uplift/anthropic-api-key`) and NO `UPLIFT_ENV_KEY`.
