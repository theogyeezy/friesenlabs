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
- **Status:** DONE @dc7a352 — grants are LIVE on Aurora: one-off `api.migrate` task (image `uplift-api:dc7a352`, task 5165fb07…) exited 0 ('schema + roles loaded'); live probe as crm_app: INSERT/SELECT/UPDATE ok, DELETE → InsufficientPrivilege on both tables; privilege matrix DELETE=false. Evidence in `infra/RUNBOOK.md`
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

### REQ-003: API task env for the live provisioning deps — master switch, Stripe/Resend secrets, webhook secret, Cognito pool id, token-signer + Anthropic admin key
- **Status:** DONE @7c94e4c (#44) — 3 containers applied + verified (`describe-secret` ×3); execution role lists the 2 exact platform ARNs; **token-signer value MINTED + stored** (CLI put, 1 version, never in git/state). GATE NOTES: `api_signup_env` stays **false** until the webhook-secret value (Stripe dashboard endpoint registration) + admin-key value (Anthropic admin key, after the # VERIFY'd endpoints are confirmed) land; flipping `api_signup_env` and then `signup_real_deps` are the two later deliberate go-live acts. Spec corrections recorded: admin-key container did not pre-exist; COGNITO_USER_POOL_ID already on the task env.
- **Requested by:** Lane Matt @feat/matt-signup-prod-deps (PR "feat(signup): real provisioning deps end-to-end (env-guarded, draft-gated)"; amended by the same PR after the adversarial review — added the `SIGNUP_REAL_DEPS` master switch + the two names the PR notes flagged as missing)
- **Needed for:** TODO INT/P0s "real Stripe adapter" / "real Resend email client" / "real Cognito admin ops" / "real email verification" — `api/prod_deps.build_signup_deps()` selects the real adapters off these env vars, but ONLY underneath the `SIGNUP_REAL_DEPS` master switch (unset = byte-identical stub boot regardless of what else is present)
- **Env/secret names** (must already exist in shared/config.py): `SIGNUP_REAL_DEPS`, `STRIPE_API_KEY`, `RESEND_API_KEY`, `STRIPE_WEBHOOK_SECRET`, `COGNITO_USER_POOL_ID`, `SIGNUP_TOKEN_SECRET_VALUE`, `ANTHROPIC_ADMIN_KEY` (all read in `shared/config.py` `Config`)
- **Spec:**
  ```hcl
  # API task definition ONLY (none of these ever reach the worker task).

  # 0) MASTER SWITCH — plain (non-secret) env var on the API task:
  #    SIGNUP_REAL_DEPS = "1"
  #    Deploy invariance (adversarial finding, HIGH): the API task ALREADY injects
  #    COGNITO_USER_POOL_ID (JWKS) and DB_* (request-path stores) for other features, so without
  #    this flag a mere image deploy of api/prod_deps.py would flip real Cognito admin calls +
  #    live-Aurora signup state. build_signup_deps selects NO real adapter unless it is exactly
  #    "true"/"1". LEAVE IT UNSET until REQ-002 (crm_app grants) is DONE and the secrets below
  #    are populated — setting it is the deliberate go-live act for the signup plane.

  # 1) EXISTING shared platform secrets -> task-def `secrets` (valueFrom):
  #    STRIPE_API_KEY <- friesenlabs/platform/shared/stripe-secret-key
  #    RESEND_API_KEY <- friesenlabs/platform/shared/resend-api-key

  # 2) NEW secret container for the Stripe webhook signing secret (value written by Lane Nick
  #    from the Stripe dashboard after registering the /webhooks/stripe endpoint; "" until then —
  #    signup/stripe_adapter.construct_event refuses ALL webhooks while it is empty).
  resource "aws_secretsmanager_secret" "stripe_webhook_secret" {
    name = "uplift/stripe-webhook-secret"
  }
  #    STRIPE_WEBHOOK_SECRET <- valueFrom aws_secretsmanager_secret.stripe_webhook_secret

  # 3) Plain (non-secret) env var on the API task, from the auth module output already in state:
  #    COGNITO_USER_POOL_ID = module.auth.user_pool_id
  #    (api/asgi.py already reads the same name for JWKS; prod_deps reuses it for the admin ops —
  #    the api task role additionally needs cognito-idp Admin* IAM, tracked in TODO INT, not here.)

  # 4) NEW secret container for the signup verification token-signing secret (HMAC key bytes;
  #    value minted by Lane Nick, e.g. `openssl rand -hex 32` — never committed anywhere).
  #    Config.signup_token_secret already names the ref ("uplift/signup-token-secret").
  resource "aws_secretsmanager_secret" "signup_token_secret" {
    name = "uplift/signup-token-secret"
  }
  #    SIGNUP_TOKEN_SECRET_VALUE <- valueFrom aws_secretsmanager_secret.signup_token_secret
  #    (empty/absent = email+phone verification stays hardcoded OFF; may_pay never flips)

  # 5) EXISTING uplift/anthropic-admin-key secret (Config.anthropic_admin_key_secret) -> task-def
  #    `secrets`:
  #    ANTHROPIC_ADMIN_KEY <- valueFrom uplift/anthropic-admin-key
  #    (the sk-ant-admin... ADMIN key, distinct from the inference key; API task ONLY — and note
  #    the # VERIFY'd workspace/key-create endpoints in signup/anthropic_admin.py must be
  #    confirmed before this is populated.)

  # IAM: grant the api task execution role GetSecretValue on the two shared platform secret ARNs
  # + the new uplift/stripe-webhook-secret + uplift/signup-token-secret ARNs + the existing
  # uplift/anthropic-admin-key ARN explicitly (TODO P2 wants uplift/* scoping TIGHTENED — list
  # these ARNs, do not widen a wildcard).
  ```
- **Done when:** `terraform validate` green; the API task definition shows `STRIPE_API_KEY` + `RESEND_API_KEY` + `STRIPE_WEBHOOK_SECRET` + `SIGNUP_TOKEN_SECRET_VALUE` + `ANTHROPIC_ADMIN_KEY` under `secrets` and `SIGNUP_REAL_DEPS` + `COGNITO_USER_POOL_ID` under `environment`; the worker task definition shows NONE of them; with `SIGNUP_REAL_DEPS=1` + values present `api.prod_deps.build_signup_deps()` selects StripeAdapter / ResendEmailSender / CognitoAdminClient / the token services — and with `SIGNUP_REAL_DEPS` absent the deploy boots byte-identically all-stub even though `COGNITO_USER_POOL_ID`/`DB_*` are present (/healthz 200). `ALLOW_REAL_SENDS` stays unset/"false" (draft-gate) — flipping it is a separate, deliberate Lane Nick act.

### REQ-005: Provisioning Lambda (signup/lambda_handler.handler) + pin the SFN Task ARNs + api-task states:StartExecution + PROVISIONING_SFN_ARN env
- **Status:** OPEN
- **Requested by:** Lane Matt @feat/matt-provisioning-lambda-sfn (PR "feat(signup): provisioning Lambda handler + SFN trigger (deterministic, claim-ordered)")
- **Needed for:** TODO INT/P1s "Package + deploy the provisioning Lambda the SFN invokes" + "Connect the Stripe webhook to start the SFN execution (decouple from the request)" (the SFN-role `Resource="*"` P2 is already closed on main — sfn_invoke now grants against the placeholder ARN; pinning `provisioning_lambda_arn` swaps both the policy and the Task states to the real ARN)
- **Env/secret names** (must already exist in shared/config.py): `PROVISIONING_SFN_ARN` (NEW, landed in `shared/config.py` with this PR); on the LAMBDA only existing names: `SIGNUP_REAL_DEPS`, `UPLIFT_DB_URL`/`DB_USER`/`DB_PASS`/`DB_HOST`/`DB_NAME`/`DB_PORT`, `COGNITO_USER_POOL_ID`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `SIGNUP_VERIFY_URL_BASE`, `ALLOW_REAL_SENDS`, `ANTHROPIC_ADMIN_KEY`
- **Spec:**
  ```hcl
  # 1) The provisioning Lambda wrapping signup/lambda_handler.py (one idempotent Provisioner
  #    step per SFN Task invocation; cold start builds clients from env via
  #    api.prod_deps.build_provisioner — the SIGNUP_REAL_DEPS master switch is honored, so an
  #    UNSET switch makes every invocation all-stub even with the env below present).
  #    Packaging: zip (or image) bundling the repo packages signup/ api/ shared/ agents/ conv/
  #    + requirements-api.txt deps (the handler's lazy api.prod_deps import pulls fastapi/
  #    psycopg2-binary/boto3; psycopg2-binary must be built for the Lambda arch).
  resource "aws_lambda_function" "provisioning" {
    function_name = "${var.project}-provisioning"
    runtime       = "python3.13"
    handler       = "signup.lambda_handler.handler"
    architectures = ["arm64"]            # match the api image arch for shared wheels
    timeout       = 60                   # one step per invocation; SFN owns retries (3, backoff)
    memory_size   = 512
    # VPC: same private subnets + an SG allowed into Aurora 5432 (the handler's PgAccountStore
    # connects as crm_app — REQ-002 grants cover the tables it touches).
    # filename / s3_* / image_uri: Lane Nick's packaging choice at apply.
    environment {
      variables = {
        # SIGNUP_REAL_DEPS — set "1" only at the deliberate signup-plane go-live (REQ-003);
        # until then the Lambda boots all-stub (deploy invariance).
        # DB_* / UPLIFT_DB_URL  <- the EXISTING crm_app credentials secret + Aurora outputs
        # COGNITO_USER_POOL_ID  = module.auth.user_pool_id
        # RESEND_API_KEY        <- friesenlabs/platform/shared/resend-api-key (draft-gated)
        # RESEND_FROM_EMAIL / SIGNUP_VERIFY_URL_BASE <- plain values
        # ALLOW_REAL_SENDS stays unset/"false" (draft-gate; flipping is a separate Nick act)
        # ANTHROPIC_ADMIN_KEY   <- uplift/anthropic-admin-key (# VERIFY'd endpoints — leave
        #                          the secret empty until signup/anthropic_admin.py is confirmed)
        # NOTE secrets must arrive as VALUES here (Lambda env, KMS-encrypted at rest) or via a
        # small in-handler Secrets Manager fetch — Lane Nick's call; either way the IAM role
        # needs GetSecretValue on exactly those ARNs (keep the uplift/* scoping TIGHT, TODO P2).
      }
    }
  }

  # 2) PIN the SFN Task ARNs: pass the deployed Lambda ARN into the EXISTING provisioning
  #    module (today every Task — and the sfn_invoke policy — points at the inert placeholder
  #    ARN; setting the var swaps both to the real function):
  #    module "provisioning" { ... provisioning_lambda_arn = aws_lambda_function.provisioning.arn }

  # 3) API-TASK IAM: allow starting the machine — scoped to the ONE machine ARN, never "*":
  #    statement {
  #      actions   = ["states:StartExecution"]
  #      resources = [module.provisioning.state_machine_arn]
  #    }

  # 4) API task definition env (plain, non-secret): the deliberate decouple switch —
  #    PROVISIONING_SFN_ARN = module.provisioning.state_machine_arn
  #    Safe default: LEAVE IT UNSET until the Lambda + machine are live and verified; unset (or
  #    SIGNUP_REAL_DEPS off) keeps api/prod_deps on the in-process provision path, byte-identical
  #    to today. Setting it flips on_paid to SfnProvisioningTrigger (deterministic execution
  #    names; re-delivery -> ExecutionAlreadyExists -> no-op), still strictly AFTER the atomic
  #    stripe_events ledger claim. Never on the worker task.
  ```
- **Done when:** `terraform validate` green with the new function + pinned `provisioning_lambda_arn` (the SFN role + every Task state reference the real function ARN, not the placeholder); the api task role policy lists `states:StartExecution` on exactly the machine ARN; the API task env shows `PROVISIONING_SFN_ARN` (once deliberately set) and the worker shows none of this; a test StartExecution with `{"account_id": <verified+paid test account>}` drives PAID -> ACTIVE through the execution history (or parks via the Catch-all on an injected failure), and a second StartExecution with the same name answers ExecutionAlreadyExists.
