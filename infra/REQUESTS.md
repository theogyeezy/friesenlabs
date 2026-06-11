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

### REQ-004: Ingestion scheduler — EventBridge schedule → Fargate one-off task running `python -m ingest.run_sync --all`
- **Status:** DONE @e67ca87 (#62) — modules/ingest APPLIED (8 pure adds): rule `uplift-ingest-nightly` live-verified DISABLED at rate(1 day); task def `uplift-ingest` renders the exact command override + INGEST_REAL_STORES/INGEST_TENANTS/INGEST_RAW_BUCKET/DB_* (API/worker defs carry zero INGEST_* names); task role live-verified scoped to `uplift/*/hubspot-*` + the deprecated shared token + `bedrock:InvokeModel` on `amazon.titan-embed-text-v2:0` (raw-bucket PutObject appears only when the var is set). GO-LIVE: set `ingest_tenants` + flip `ingest_schedule_enabled=true` in tfvars + targeted apply. Batch-backfill IAM deferred to its own REQ per the spec.
- **Requested by:** Lane Matt @feat/matt-ingest-runnable-creds-batch (PR "feat(ingest): scheduler entrypoint, per-tenant creds, SET LOCAL cursors, Titan batch")
- **Needed for:** TODO AI/P1 "Build the ingestion scheduler/infra (connectors → chunk → embed → pgvector)" — the code half (`ingest/run_sync.py`) ships with this request; also unblocks INT/P1 per-tenant connector creds (the task role needs the per-tenant secret reads) and, later, AI/P2 Titan batch backfill.
- **Env/secret names** (must already exist in shared/config.py): `INGEST_REAL_STORES`, `INGEST_TENANTS`, `INGEST_RAW_BUCKET`, `INGEST_BATCH_S3_BUCKET`, `BEDROCK_BATCH_ROLE_ARN` (all appended in this PR), plus the EXISTING `DB_USER`/`DB_PASS`/`DB_HOST`/`DB_NAME`/`DB_PORT` (crm_app DSN, same values the worker gets per REQ-001).
- **Spec:**
  ```hcl
  # 1) A dedicated INGEST task definition (same image as the API; arm64), command override:
  #      command = ["python", "-m", "ingest.run_sync", "--all"]
  #    Environment (NONE of these ever reach the API or worker task definitions):
  #      INGEST_REAL_STORES = "1"            # the deliberate act — set ONLY on this task; unset
  #                                          # anywhere else keeps run_sync a no-op offline stub
  #      INGEST_TENANTS     = var.ingest_tenants   # comma-separated tenant ids; safe "" default
  #                                          # ("" => run_sync logs 'nothing to do' and exits 0)
  #      INGEST_RAW_BUCKET  = var.ingest_raw_bucket # safe "" default (raw landing skipped w/ warn)
  #      DB_USER / DB_PASS  <- valueFrom the EXISTING crm_app credentials secret
  #      DB_HOST / DB_NAME / DB_PORT <- existing Aurora outputs (same as the API/worker tasks)
  variable "ingest_tenants" {
    type    = string
    default = ""
  }
  variable "ingest_raw_bucket" {
    type    = string
    default = ""
  }

  # 2) EventBridge schedule (DISABLED by default — flipping it on is the go-live act) targeting
  #    ecs:RunTask of the ingest task definition on the existing cluster, e.g. nightly:
  variable "ingest_schedule_enabled" {
    type    = bool
    default = false
  }
  #    aws_cloudwatch_event_rule  schedule_expression = "rate(1 day)"
  #                               state = var.ingest_schedule_enabled ? "ENABLED" : "DISABLED"
  #    aws_cloudwatch_event_target -> ecs_target (LATEST task def, awsvpc, private subnets,
  #                               the API service SG so Aurora ingress already matches)

  # 3) IAM (the ingest TASK ROLE — not the API/worker roles):
  #      secretsmanager:GetSecretValue on arn:...:secret:uplift/*/hubspot-*   (per-tenant pattern,
  #        ingest/connectors/base.py tenant_secret_ref) AND the DEPRECATED shared
  #        uplift/hubspot-private-app-token (until every tenant is migrated)
  #      bedrock:InvokeModel on the Titan V2 embeddings model (synchronous embed path)
  #      s3:PutObject on ${var.ingest_raw_bucket}/raw/*           (raw lake, when set)
  #    LATER (AI/P2 batch backfill — may ship as its own REQ when first used):
  #      bedrock:CreateModelInvocationJob + GetModelInvocationJob, iam:PassRole on
  #      var.bedrock_batch_role_arn, s3 RW on var.ingest_batch_s3_bucket (batch-embed/* JSONL I/O).
  variable "bedrock_batch_role_arn" {
    type    = string
    default = ""
  }
  variable "ingest_batch_s3_bucket" {
    type    = string
    default = ""
  }
  ```
- **Done when:** `terraform validate` green with safe defaults (schedule DISABLED, "" vars); the ingest task definition shows `INGEST_REAL_STORES=1` + `INGEST_TENANTS` + `INGEST_RAW_BUCKET` + `DB_*` and the API/worker task definitions show NONE of the `INGEST_*` names; with the schedule flipped on and a tenant id + HubSpot secret populated, a scheduled run populates `documents` for that tenant and the next run embeds ~0 (the TODO's done-when). Until then `python -m ingest.run_sync --tenant <id>` stays runnable anywhere as an offline stub (exit 0, touches nothing).

### REQ-005: Provisioning Lambda (signup/lambda_handler.handler) + pin the SFN Task ARNs + api-task states:StartExecution + PROVISIONING_SFN_ARN env
- **Status:** DONE @e55dcc4 (#65) — APPLIED + SMOKED: `uplift-provisioning` Lambda live (arm64 image `uplift-provisioning:e55dcc4`, VPC'd, all-stub since SIGNUP_REAL_DEPS unset); SFN Task states + `sfn_invoke` pinned to the real ARN; api task role has `states:StartExecution` on exactly the machine ARN. Smoke: StartExecution invoked the Lambda cleanly (cold start + handler ran; failed correctly on the nonexistent test account with SFN retries + Catch park — packaging/wiring/retry/park-path proven), duplicate execution name → `ExecutionAlreadyExists` (idempotency proven). The full verified+paid PAID→ACTIVE drive rides the signup go-live (needs SIGNUP_REAL_DEPS + Stripe values). `PROVISIONING_SFN_ARN` stays un-injected (`api_provisioning_sfn=false`) — flipping it is the deliberate decouple act in the RUNBOOK go-live sequence.
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

### REQ-006: Server-side PostHog funnel env (POSTHOG_PROJECT_KEY_VALUE / POSTHOG_HOST) + tenant_settings migrate note
- **Status:** DONE (#85) — POSTHOG_PROJECT_KEY_VALUE live on the provisioning Lambda env (verified) + staged in the api_signup_env gate (lands with the signup go-live roll — deviation noted: avoids an extra api roll now); execution role lists the exact posthog ARN; POSTHOG_HOST plain-var override available. tenant_settings: no GRANT needed per the spec; the table reaches live Aurora on the next one-off migrate run with a fresh image.
- **Requested by:** Lane Matt @feat/matt-signup-posthog-tenant-defaults (PR "feat(signup): server-side PostHog funnel + tenant defaults + retry-provision route")
- **Needed for:** TODO INT/P3 "Wire the server-side PostHog funnel client" (payment_succeeded / instance_provisioned / provisioning_failed captured server-side, grouped by tenant) + INT/P2 "Provisioning tenant-context correctness" (the new `tenant_settings` table seeded at provisioning step 5)
- **Env/secret names** (must already exist in shared/config.py): `POSTHOG_PROJECT_KEY_VALUE`, `POSTHOG_HOST` (both appended with this PR); `COGNITO_CLIENT_ID` (EXISTING name api/asgi.py already reads — now also surfaced via `Config.cognito_client_id` for the retry-provision claims gate; no new wiring needed if it is already on the task env)
- **Spec:**
  ```hcl
  # 1) API task definition + the provisioning Lambda env (REQ-005), `secrets` block:
  #    POSTHOG_PROJECT_KEY_VALUE <- valueFrom the EXISTING shared platform secret
  #        friesenlabs/platform/shared/posthog-project-key   (THE SOURCE of the key — the
  #        resolved VALUE lands under this NEW deliberate env name; never the SM reference,
  #        never committed). IAM: add this one secret ARN to the execution role's
  #        GetSecretValue list (keep the scoping TIGHT — list ARNs, no wildcards).
  #    Deploy invariance: signup/posthog_client.py is selected ONLY under the SIGNUP_REAL_DEPS
  #    master switch AND this env — unset, the build stays byte-identical (funnel = None).
  #    A PostHog project key is write-only for event capture (not an account credential), but
  #    treat it as a secret anyway: it rides the shared platform secret, not plain env.

  # 2) OPTIONAL plain (non-secret) env on the same two runtimes:
  #    POSTHOG_HOST = "https://us.i.posthog.com"   # the in-code default; set only to override
  #                                                # (EU cloud / self-hosted ingestion)

  # 3) NO new GRANT for tenant_settings: db/roles.sql's ALTER DEFAULT PRIVILEGES already hands
  #    crm_app SELECT/INSERT/UPDATE/DELETE on new tables in schema public. ORDERING NOTE: the
  #    next one-off `api.migrate` Fargate task must run (creates tenant_settings + FORCE'd RLS
  #    policy) BEFORE the signup plane goes live with a DSN — provisioning step 5 INSERTs into
  #    it under SIGNUP_REAL_DEPS, and a missing table parks the account (rollback-safe, but
  #    operational noise). Worth a live probe after migrate: as crm_app with
  #    `SET app.current_tenant`, INSERT + SELECT a tenant_settings row; without the GUC, 0 rows.
  ```
- **Done when:** the API task definition + provisioning Lambda show `POSTHOG_PROJECT_KEY_VALUE` under `secrets` (and the worker/cube/ingest tasks show it NOWHERE); with SIGNUP_REAL_DEPS=1 + the key populated, a staging payment produces `payment_succeeded` + `instance_provisioned` grouped under the tenant in PostHog (the INT/P3 done-when) and a forced failure produces `provisioning_failed`; `api.migrate` has been re-run and the crm_app tenant_settings probe above passes; with the key absent the deploy boots byte-identically (funnel None, no network).

### REQ-008 (renumbered by Nick — duplicate REQ-006 header): api-task IAM Secrets Manager WRITE on the per-tenant connector slots (`uplift/*/hubspot`) + `INTEGRATIONS_REAL_SECRETS` env
- **Status:** DONE (#85) — `connector-write` live on the api task role, verified scoped to exactly uplift/*/hubspot* (Put/Create/Describe). INTEGRATIONS_REAL_SECRETS stays unset behind var api_integrations_real — flipping it is the deliberate act once Matt's integrations routes are exercised; the # VERIFY-on-first-connect note stands.
- **Requested by:** Lane Matt @feat/matt-integrations-api (PR "feat(api): integrations endpoints — list/credentials/sync (claims-bound, gated)")
- **Needed for:** TODO INT/P2 "Build the real integrations/connect UI + backend" — the api half (`api/integrations_routes.py`): `POST /integrations/{name}/credentials` vaults a tenant's HubSpot token into `uplift/{tenant_id}/hubspot` (the `ingest/connectors/base.py tenant_secret_ref` slot the REQ-004 ingest task already READS); `GET /integrations` answers connection status via DescribeSecret (never the value).
- **Env/secret names** (must already exist in shared/config.py): `INTEGRATIONS_REAL_SECRETS` (NEW, landed in `shared/config.py` with this PR). Safe default UNSET = the writer is a stub: credentials POST answers an honest 503, status reads "unknown" — byte-identical boot regardless of what other env the task carries.
- **Spec:**
  ```hcl
  # 1) API-TASK IAM: write + existence-check on EXACTLY the per-tenant hubspot slots — never
  #    uplift/* broadly (the env-id/admin-key/demo-user secrets stay out of reach). Secrets
  #    Manager appends a random 6-char suffix to secret ARNs, hence the trailing wildcard.
  #    # VERIFY on first live connect: CreateSecret resource-scoping matches the name pattern.
  statement {
    actions = [
      "secretsmanager:PutSecretValue",   # rotate path (slot already exists)
      "secretsmanager:CreateSecret",     # first-connect path (slot does not exist yet)
      "secretsmanager:DescribeSecret",   # GET /integrations status — existence only, no value
    ]
    resources = ["arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:uplift/*/hubspot*"]
  }

  # 2) API task definition env (plain, non-secret): the deliberate master switch —
  #    INTEGRATIONS_REAL_SECRETS = "1"
  #    Safe default: LEAVE IT UNSET until this REQ's IAM is applied; unset keeps the all-stub
  #    behavior (honest 503s). Exactly "true"/"1" — anything else fails closed. API task ONLY,
  #    never the worker.

  # 3) NO INGEST_* names on the API task (unchanged — REQ-004 done-when stands). The new
  #    POST /integrations/{name}/sync therefore answers an honest 503 on the live API; the
  #    EventBridge-scheduled one-off task stays the primary sync path. Wiring API-kicked syncs
  #    (INGEST_REAL_STORES + DB_* + bedrock:InvokeModel on the API task) is a SEPARATE,
  #    deliberate future REQ — do not flip it as part of this one.
  ```
- **Done when:** `terraform validate` green; the api task role policy lists exactly the three actions above scoped to `uplift/*/hubspot*` (and the worker/ingest roles are unchanged); with `INTEGRATIONS_REAL_SECRETS=1` on the API task, `POST /integrations/hubspot/credentials` (authed) creates/updates `uplift/<that tenant>/hubspot` in Secrets Manager, `GET /integrations` flips that tenant's hubspot status to `connected`, and the next REQ-004 scheduled ingest run for that tenant resolves the per-tenant secret WITHOUT the deprecated shared-token fallback warning.

### REQ-007: CI job for the gated live signup e2e (Stripe TEST mode) — `tests/integration/test_signup_live_e2e.py`
- **Status:** DONE (#85) — live-signup-e2e job in ci.yml (main pushes + nightly 09:17 UTC cron, never PRs); without the STRIPE_TEST_* GH secrets every test self-skips green. Set the secrets (gh secret set STRIPE_TEST_SECRET_KEY etc.) when test-mode keys exist — user input.
- **Requested by:** Lane Matt @feat/matt-live-e2e-cube-dims-synth-refs (PR "feat(tests): gated live signup e2e + cube dimension_values + synthesizer ref normalization")
- **Needed for:** TODO INT/P2 "gated live e2e" — continuously proving signup → email-token verify → OTP verify → Stripe TEST-mode Checkout → SIGNED webhook → idempotent provisioning to ACTIVE against real sandbox providers, instead of once by hand. The test file ships with this PR; it skips itself cleanly (every test) when the secrets below are absent, so the existing offline pytest job is untouched either way.
- **Env/secret names** (must already exist in shared/config.py): n/a — the gates are TEST-HARNESS-ONLY env (`STRIPE_TEST_SECRET_KEY`, `STRIPE_TEST_WEBHOOK_SECRET`; optional `STRIPE_TEST_PRICE_ID` / `SIGNUP_E2E_COGNITO_POOL_ID` / `SIGNUP_E2E_RESEND_API_KEY`). Deliberately NOT in `shared/config.py`: no app code reads them and no live task may ever inject them (deploy invariance) — they exist only as GitHub Actions secrets fed to this one pytest invocation. `ALLOW_REAL_SENDS` stays UNSET (the module fails loudly if it is "true"; every sender in the harness is constructed draft-gated).
- **Spec:**
  ```yaml
  # .github/workflows/ci.yml (Lane Nick's file) — a SEPARATE job so the offline suite never
  # depends on repo secrets. Suggested triggers: push to main + a nightly cron; NOT fork PRs
  # (GitHub withholds secrets there anyway — the file then skips every test cleanly).
  live-signup-e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.13" }
      # the `stripe` lib lives in requirements-api.txt (it is NOT a dev dependency)
      - run: pip install -r requirements-api.txt -r requirements-dev.txt
      - run: python -m pytest tests/integration/test_signup_live_e2e.py -q
        env:
          STRIPE_TEST_SECRET_KEY: ${{ secrets.STRIPE_TEST_SECRET_KEY }}          # sk_test_… ONLY —
          # the module hard-fails on a non-test-mode key (it must never touch live money)
          STRIPE_TEST_WEBHOOK_SECRET: ${{ secrets.STRIPE_TEST_WEBHOOK_SECRET }}  # whsec_… (test mode)
          # Optional sandbox seams (leave unset to keep those seams on the offline fakes):
          # STRIPE_TEST_PRICE_ID / SIGNUP_E2E_COGNITO_POOL_ID / SIGNUP_E2E_RESEND_API_KEY
          # ALLOW_REAL_SENDS: never set here — the draft-gate is part of what the e2e proves.
  ```
- **Done when:** the job is green on `main` with the two `STRIPE_TEST_*` GitHub secrets populated from the Stripe TEST-mode dashboard (test-mode key + a test-mode webhook signing secret; the run creates only test-mode objects — a throwaway $1/mo product+price unless `STRIPE_TEST_PRICE_ID` is supplied); with the secrets absent the job (and any local run) reports the file's tests as SKIPPED, never failed; the offline pytest job is unchanged.

### REQ-009: api-task IAM Step Functions READ (`states:ListExecutions`) scoped to the provisioning state machine
- **Status:** PENDING — the api task role today carries `states:StartExecution` ONLY (REQ-005); no read action exists. Until this lands, `GET /workflows` serves the static diagram and degrades the run feed to an honest `200 {executions_available: false, reason: "pending IAM grant (REQ-009)"}` (verified: AccessDenied is caught and never surfaced). Applying this flips the feed live with NO app redeploy.
- **Requested by:** Lane Product @feat/prod-workflows (PR "feat(workflows-tab): real provisioning-machine view — owned SFN diagram + recent executions")
- **Needed for:** the real Workflows tab (`api/workflows_routes.py`): `GET /workflows` lists the provisioning machine's recent executions (name + status + start/stop timestamps ONLY — `list_executions`, capped at 20). NO `DescribeStateMachine`, NO `DescribeExecution`, NO `Get*ExecutionHistory`: execution input/output stays unreadable by design, and the static step diagram is OWNED code (never a live Describe). So the read surface is exactly one action.
- **Env/secret names** (must already exist in shared/config.py): `PROVISIONING_SFN_ARN` (the machine ARN; already wired into the api task by REQ-005 for StartExecution and read by `WorkflowsDeps` in `api/asgi.py`). No NEW env — the route lights up purely on the IAM grant below.
- **Spec:**
  ```hcl
  # API-TASK IAM: read-only list of executions on EXACTLY the provisioning machine — never
  # states:* broadly, and never a Describe/History action (those would expose execution
  # input/output + the raw ASL with Lambda ARNs). ListExecutions is account-id-bearing in its
  # response (executionArn/stateMachineArn) but the route strips every ARN server-side before
  # serialization (proven in tests/integration/test_api_workflows.py).
  statement {
    actions   = ["states:ListExecutions"]
    resources = [var.provisioning_sfn_arn]  # the uplift-provisioning stateMachine ARN (REQ-005)
  }
  # Note: ListExecutions is authorized on the STATE MACHINE arn (arn:aws:states:…:stateMachine:…),
  # NOT an execution arn — the existing PROVISIONING_SFN_ARN is the correct resource as-is.
  ```
- **Done when:** `terraform validate` green; the api task role policy lists `states:ListExecutions` scoped to exactly the provisioning stateMachine ARN (and `StartExecution` from REQ-005 is unchanged; no Describe/History action appears on any role); against the live API, an authed `GET /workflows` returns `executions_available: true` with a `recent_executions` array (name/status/timestamps only — no `arn:` fragment, no account id in the body), and with the grant absent the same call still returns `200` with `executions_available: false, reason: "pending IAM grant (REQ-009)"`.

---

### REQ-010: GRANT crm_app DML on the per-tenant `onboarding_state` table (first-run / onboarding)
- **Status:** OPEN — already mirrored into `db/roles.sql` (the GRANT/REVOKE pair) so the static grant gate (`tests/unit/test_sql_schema.py`) and the integration DB harness (which loads `db/schema.sql` + `db/roles.sql`) stay green; this entry documents the same SQL for Lane Nick to confirm on the live Aurora migration. Per `CONTRIBUTING.md`, `db/roles.sql` is Nick-only; the edit here is the necessary cross-lane GRANT for a new tenant table — please review on the live `api.migrate` run.
- **Requested by:** Lane Onboarding @feat/cust-onboarding (PR "feat(onboarding): first-run experience — empty states, guided checklist, load-sample data")
- **Needed for:** the onboarding routes (`api/onboarding_routes.py`): `GET/PUT /onboarding` (per-tenant first-run checklist state) + `POST /onboarding/load-sample` (one-click demo-fixture load into the calling tenant). All RLS-scoped via `SET LOCAL app.current_tenant`; the route upserts the tenant's `onboarding_state` row.
- **Env/secret names** (must already exist in shared/config.py): n/a (GRANT only; the route rides the existing crm_app DSN — `dsn_from_env()` — the same one `/contacts`, `/deals`, `/views` already use).
- **Spec:**
  ```sql
  -- For db/roles.sql (Lane Nick's file). onboarding_state is a RLS-FORCEd tenant table (see
  -- schema.sql: tenant_id PRIMARY KEY, in the tenant_tables array, explicit ENABLE/FORCE + policy).
  -- crm_app needs SELECT/INSERT/UPDATE (the upsert), NEVER DELETE: a tenant's onboarding row is
  -- durable upserted state, never erased by the app. Same fresh-load reason as
  -- tenant_workspaces/tenant_settings (schema.sql runs before roles.sql, so ALTER DEFAULT
  -- PRIVILEGES never covers it — without this line crm_app has ZERO privileges on a fresh load).
  GRANT SELECT, INSERT, UPDATE ON onboarding_state TO crm_app;
  REVOKE DELETE ON onboarding_state FROM crm_app;
  ```
- **Done when:** the live `api.migrate` one-off runs `db/roles.sql` clean; a crm_app probe can SELECT/INSERT/UPDATE its own `onboarding_state` row under `SET LOCAL app.current_tenant` and is denied DELETE; `GET /onboarding` returns the tenant's row (or the honest default) on the live API.
