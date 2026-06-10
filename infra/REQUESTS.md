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

### REQ-001: AI-plane env wiring — `uplift/env-id` secret, worker task-def env, org Anthropic key to the API task ONLY
- **Status:** OPEN
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
