# Security audit — release-readiness (2026-06-11, Lane Matt)

Full-surface security audit ahead of opening the product to real paying customers.
Method: 5 parallel deep-dive reviews (API auth/tenancy/RLS · signup/billing/Stripe ·
agent plane/Greenlight/worker · web/public endpoints/ingest · Terraform per the AWS
security-review checklist) + a Semgrep `p/security-audit` + `p/secrets` scan (8 findings,
all triaged — no new issues beyond the agent reviews; the `ml/artifacts.py` pickle hit is
the migration-only `deserialize_legacy_v1`, and the serving path HMAC-verifies before any
deserialization). Read-only audit; no live AWS touched. Branch: `feat/matt-security-audit`.

**Bottom line: the core security architecture is genuinely sound — every product invariant
(Trust Rule, FORCEd RLS over a non-owner role, draft-only Greenlight, spec-not-code,
webhook-only provisioning, secrets hygiene) was verified as implemented and holding in
code.** No remotely-exploitable Critical was found in the app. The gaps that matter for
customer release: one Critical infra item (CI/CD role = `AdministratorAccess`), the absence
of intra-tenant RBAC, and a cluster of latent/medium defense-in-depth items listed below.
Actionable TODOs: `TODO.md` § "Security audit — TODOs (2026-06-11, Lane Matt)".

---

## Findings

### Critical

**C1 — GitHub OIDC deploy role has `AdministratorAccess`.**
`infra/modules/iam/main.tf:319-322` attaches the AWS-managed `AdministratorAccess` policy to
`uplift-deploy`, the role GitHub Actions assumes via OIDC. The trust policy is correctly
pinned (`repo:theogyeezy/friesenlabs:ref:refs/heads/main` + `:environment:production`,
`aud=sts.amazonaws.com` — `:307-309`), but the permission side is unbounded: a compromised
workflow/action dependency or a malicious commit that reaches `main` is full account
takeover. The in-code comment (`:283-284`) already records this as a follow-up — it should
block release instead. Fix: scoped deploy policy (ECS/ECR + `iam:PassRole` on the exact
task/exec role ARNs, CloudFront, S3 state/asset buckets, `secretsmanager` read on `uplift/*`)
or at minimum a permissions boundary.

### High

**H1 — No intra-tenant RBAC: every authed tenant user is a full tenant admin.**
`api/routes_control.py:17-30` (documented v1 decision), `api/billing_routes.py:59-96`,
`api/modules_routes.py:88-109`, `api/account_delete_routes.py:71-99`. The Cognito ID token
carries only `custom:tenant_id`/`sub`/`email` — no role/group claim — and no route checks
one. Any tenant user (e.g. a sales rep) can: disable the tenant **kill switch**, raise
**autonomy to L3**, open the **Stripe billing portal** (cancel subscription), change
**module entitlements that move charges**, and trigger **GDPR export**. `POST /account/delete`
is in the same class but currently inert (asgi default → 503). Fine for single-user tenants;
not for teams. Fix: add a Cognito group/role claim and gate the privileged routes on admin.

**H2 — `ALLOW_ADMIN_USER_PASSWORD_AUTH` enabled on the production SPA client.**
`infra/modules/auth/main.tf:74`. Lets any holder of `cognito-idp:AdminInitiateAuth` exchange
username+password for tokens, bypassing the Hosted-UI/PKCE design. Comment says
smoke-tests-only, but it's a permanent property of the prod client (and compounds C1). Fix:
drop the flow from the prod client; use a separate non-public test client.

**H3 — No Cognito advanced security / threat protection.**
`infra/modules/auth/main.tf` has no `user_pool_add_ons` block — no compromised-credential
detection or adaptive auth on the pool fronting every tenant. (Password policy 12-char,
deletion protection ACTIVE, admin-create-only all confirmed good.) Verify the
provider-current attribute name for `~> 6.49` before applying.

**H4 — No VPC flow logs.** No `aws_flow_log` anywhere in `infra/`. GuardDuty is on but has
no flow-log signal to consume; network forensics are impossible.

**H5 — No WAF logging.** `infra/modules/api_cdn/main.tf:27-97` builds the WAFv2 ACL (managed
rules + 2000/5-min rate rule confirmed) but there is no
`aws_wafv2_web_acl_logging_configuration` — no forensics on edge allow/block decisions.

**H6 — Aurora not on a customer-managed KMS key; Performance Insights has no KMS key.**
`infra/modules/data/main.tf:25` (`storage_encrypted=true`, default `aws/rds` key), `:56`
(PI enabled, no `performance_insights_kms_key_id` — PI captures query text, which can carry
tenant PII in literals). For the most sensitive datastore in a multi-tenant CRM, move both
to a rotating CMK. **Caution: changing `kms_key_id` forces cluster replacement** — this is a
snapshot-restore migration; never let a plan propose it silently.

### Medium

**M1 — Compliance gate (TCPA/CAN-SPAM) only runs on the ActionGate path; not re-run after
human edit.** `api/control/compliance.py:41` has exactly one call site
(`api/control/gate.py:44`). Proposals created by the worker (`agents/tools/base.py:94`),
Sidecar accept (`api/sidecar_routes.py:94`), and the playbook runner skip it, and
`apply_approved_action` (`api/app.py:283-284`) never re-validates — including after an
`edit` decision mutates recipient/body (`api/control/greenlight.py:238-251`). Masked today
because `send_email`/`issue_quote` appliers are `record_only` — this becomes **High the day
a real sender lands in `APPLIERS`**. Fix: run `compliance.validate` inside
`Greenlight.propose` (keyed off `tool_meta(name)["channel"]`) and re-validate post-edit
before apply.

**M2 — The "refuse to boot with the Stripe bypass in prod" guard is dead code in prod.**
`shared/config.py:228-245`: `is_prod()` reads `UPLIFT_ENVIRONMENT`, which is never set on
any live task (no hit in `infra/`). So `assert_bypass_not_enabled_in_prod()` can never fire;
the only thing keeping `SIGNUP_INTERNAL_BYPASS_DOMAINS` (comped provisioning) off is its
empty default. Fix: set `UPLIFT_ENVIRONMENT=prod` on the API + provisioning-Lambda task envs.

**M3 — SPA has no CSP or security headers; Cognito refresh token in localStorage.**
The `security_headers_config` (`infra/modules/api_cdn/main.tf:141-161`) covers only the API
distribution; the Amplify app (`infra/modules/web_hosting/main.tf:53-111`) emits no CSP,
`frame-ancestors`, nosniff, or referrer-policy. Tokens — including the ~30-day refresh
token — live in localStorage (`web/src/auth/core.js:25`, a documented tradeoff whose stated
sole XSS defense is the DOMPurify sink). With no CSP there is zero defense-in-depth against
a dependency-supply-chain or slipped-XSS exfil, and the authed app is clickjackable. Fix:
Amplify `custom_headers`/`customHttp.yml` with CSP + standard headers; consider moving the
refresh token out of localStorage.

**M4 — Vega chart `spec` fragment is not key-whitelisted.**
`web/src/dashboard/viewSpec.ts:351-360` validates `spec` only as "is an object";
`SpecRenderer.tsx:297-304` spreads it into vega-embed. Containment is currently good
(SVG renderer, `actions:false`, loader disabled, inline data forced), but spec-not-code here
rests on vega-embed's own sanitization instead of the closed catalog used everywhere else.
Fix: whitelist fragment keys (`mark`/`encoding`/vetted `transform` ops; reject
`params`/`signals`/`href`/`usermeta`/`data`), mirror in
`shared/schemas/view_spec.schema.json`, pin vega versions.

**M5 — No trust boundary around tenant data entering agent prompts (prompt injection).**
RAG chunks (`conv/synthesizer.py:170-177`), coordinator tool results
(`agents/runtime.py:434-460`), and event-playbook payloads
(`agents/playbooks/runner.py:125-126` — payloads can originate from `POST /public/leads`
content) are concatenated into prompts with no untrusted-data delimiting. Injected content
cannot bypass the human gate or RLS, but can steer drafts a human then rubber-stamps. Fix:
delimit retrieved/CRM/lead content with explicit treat-as-data framing.

**M6 — Email-verification token single-use store is per-task in-memory.**
`api/prod_deps.py:591-595` builds `EmailTokenService` without a shared `used_store`
(defaults to in-memory; the OTP path correctly uses `PgOtpStore`). With 2+ tasks a token is
replayable cross-task within its 15-min TTL. Low real impact (replay just re-sets
`email_verified=True`) but diverges from the documented single-use invariant. Fix: Pg-backed
used-token store.

**M7 — CAPTCHA not yet enabled (code landed mid-audit in #248; ops flip remains).**
PR #248 wired real Turnstile/hCaptcha siteverify validators, auto-selected from env
(`signup/abuse.py` `CaptchaVerifier.from_env`; required-without-validator fails closed by
documented design). Until the owner creates the Turnstile site, sets `TURNSTILE_SECRET` on
the API task, flips `SIGNUP_CAPTCHA_REQUIRED=true`, and adds the widget to the signup form,
there is still no bot defense at signup beyond the WAF rate rule + in-process velocity
limiter. Set the secret BEFORE the flag — the flag alone blocks all signups.

**M8 — Global kill-switch operator gate is tenant-granular, not user-granular.**
`api/routes_control.py:66-69,107-111`: `CONTROL_GLOBAL_OPERATOR_TENANTS` holds tenant UUIDs —
combined with H1, **every user** in an operator tenant can pause/unpause the whole platform.
(Fail-closed on unset confirmed.) Fix: platform-admin identity, not tenant membership.

**M9 — ECS hardening gaps.** No `readonlyRootFilesystem`/non-root `user` on any container;
ADOT sidecar floats on `:latest` (`api_service/main.tf:303`, `cube/main.tf:84`,
`worker/main.tf:127`); `enable_execute_command=true` permanently on the API service with no
`execute_command_configuration` session-log sink (`api_service/main.tf:333`). Fix: pin ADOT
by digest, add read-only root + non-root user, gate ECS Exec behind a var + log sessions.

**M10 — Shared `sg_api` across api/cube/worker/ingest/provisioning-Lambda + cube :4000
self-rule** (`infra/modules/security/main.tf:116-124`): a compromised worker or Lambda can
reach cube:4000 and Aurora:5432 directly. RLS + Cube security context still gate data; this
is least-privilege tier isolation. Fix: split cube into its own SG with an explicit
api→cube:4000 rule.

### Low

- **L1** PII in logs: full email at info/warn (`signup/resend_sender.py:128,149,152`), full
  phone (`signup/sms_sender.py:59,82`). Mask.
- **L2** Worker builds an org-key Anthropic client if `ANTHROPIC_API_KEY` is ever
  misconfigured onto the task (`worker/worker.py:203-206`); assert-absent at startup instead.
- **L3** Raw `account_id` still accepted as bare bearer during the session-token rollout
  (`shared/signup_session.py:109-136`) — UUIDv4, unguessable, no tenant_id leak; finish the
  rollout and drop the fallback (already tracked in the signup TODOs).
- **L4** Raw `innerHTML` sink on the landing constellation
  (`web/src/screens/landing-constellation.tsx:228,238`) — static constants today, violates
  the SafeHtml-only rule; convert to `textContent`.
- **L5** CSV cells are not formula-escaped (`ingest/connectors/csv_import.py`) — latent
  (no CSV/XLSX export path exists today; `/account/export` is JSON); escape `=+-@` leaders on
  any future tabular export. Imported text also lands in the RAG corpus → see M5.
- **L6** Public per-IP rate limiting trusts XFF at `trusted_hops=2`
  (`api/public_routes.py:137-159`) — correct iff the X-Origin-Verify 403-default covers
  every ALB listener path; verify on any listener change.
- **L7** JWT `token_use` accepts `None` (`api/auth.py:65-66`) and a few 422 paths echo
  `str(e)` (`api/app.py:351,363,396,431`; `api/signup_routes.py:268,276`). Tighten both.
- **L8** X-Origin-Verify secret has no rotation story (`infra/modules/secrets/main.tf:49-64`);
  document/automate regenerate→CloudFront header→ALB rule.
- **L9** ALB/CloudFront log buckets on SSE-S3 not KMS (`alb/main.tf:39`,
  `api_cdn/main.tf:119`) — likely an AWS constraint for ALB delivery; confirm + document.

### Already tracked elsewhere (not re-added)

Org SCP guardrails (TODO L534 follow-up), Redis AUTH (L562), public-repo account-id/hostname
exposure (L564), GH Actions SHA-pinning (L566), Aurora `rds.force_ssl` (L549), shared
execution role (L548), SG egress allow-all (L553, accepted for NAT egress), in-process
velocity limiter + rate-limit fail-open (documented design; WAF is the flood gate).

### Verify-items (assumptions that are load-bearing)

- **V1** Cube #177 fix uses session-level `set_config` and is safe **only because** Cube keys
  driver pools per-tenant via `contextToAppId`/`driverFactory`
  (`semantic/security.js:144-198`) — confirm against the deployed Cube image.
- **V2** `sessions.retrieve` under the env key returns the API-stamped tenant metadata
  (in-code VERIFY, `worker/worker.py:262-274`) — confirm live.

---

## Invariants verified as holding (with evidence)

- **THE TRUST RULE** — tenant only from the verified `custom:tenant_id` claim
  (`api/auth.py:84-95`); no route/body/header tenant override anywhere; Cube/slot adapters
  refuse mismatched caller-supplied tenant (`api/pg_clients.py:924-935`); client never sends
  tenant_id (`web/src/api/client.ts`).
- **JWT verification** — RS256 pinned, JWKS cached, `aud`/`iss`/`exp` required, fails closed
  to 401; `_RejectAll` (never allow-all) when pool unset (`api/auth.py:44-67`,
  `api/asgi.py:97-110`).
- **FORCEd RLS + non-owner role** — all 16 tenant tables ENABLE+FORCE + tenant policy
  (`db/schema.sql:330-602`); `crm_app` NOSUPERUSER/NOBYPASSRLS (`db/roles.sql:12-23`);
  composite `(tenant_id,id)` FKs close the FK-bypass hole (`db/schema.sql:474-535`); live
  isolation gate PASSED as `crm_app`.
- **`SET LOCAL` per-op tx in every store** — no session-level SET, no GUC leakage across
  pooled connections (`api/pg_clients.py:271-289`, `api/control/greenlight.py:132-150`,
  `api/views.py:48-60`).
- **No SQL injection** — all dynamic identifiers from hardcoded allow-lists, values always
  bound; LIKE terms escaped (`pg_clients.py:93-112,414,734,763`; `ingest/sinks.py:253-309`).
- **Draft-only on ALL paths** — `ALWAYS_ASK` tools draft via Greenlight even on the AUTO
  branch (`agents/tools/base.py:80-102`, `api/control/gate.py:68`); comms appliers
  `record_only` (`api/control/appliers.py:82-93`); worker registers no `SendEmail`.
- **Approval TOCTOU-safe** — pending→decided is an atomic CAS; executed payload is the
  decided snapshot read back post-update (`api/control/greenlight.py:233-257`,
  `api/app.py:262-284`); trusted action name wins over client payload (`:208-215`).
- **Kill switch fail-closed; autonomy bounded 0-3** (`api/control/settings.py:130-138`,
  `api/routes_control.py:136`); global scope operator-gated, fail-closed on unset.
- **View-spec validated server-side on every save/patch path; spec-not-code; renderer is a
  closed catalog** (`api/app.py:345-431`, `conv/view_patcher.py:169-176`,
  `shared/view_spec.py:113-146`; client: SafeHtml/DOMPurify single sink, Vega SVG +
  `actions:false` + loader disabled).
- **Cube tenant scoping everywhere** — per-request HS256 JWT (60s TTL, alg pinned,
  constant-time compare) + server-side `queryRewrite` force-filter + #177 parameterized
  `set_config` on per-tenant pools (`agents/tools/cube_client.py`, `semantic/security.js`).
- **Traces minimized + tenant-scoped** — RLS + route re-check; raw inputs/outputs not
  serialized out; 200-char truncation at write (`api/control/traces.py`,
  `api/routes_control.py:72-83`).
- **Stripe trust model** — `construct_event` with no skip-verify fallback
  (`signup/stripe_adapter.py:276-288`); two-layer idempotency (event ledger + settle CAS);
  livemode/customer/price/session field verification before settlement
  (`signup/payment.py:249-430`); server-side plan→price mapping (client cannot supply a
  price_id); verify-email+phone before pay enforced at checkout, comp, and provisioning.
- **Internal bypass correctly gated** — server-stored verified email, exact lowercased set
  membership, off by default, re-checks `may_pay` (`api/signup_routes.py:258-269`).
- **Workspace-key pool hygiene** — PG holds only SM ref + sha256 + last-4; loader writes SM
  first; startup + consume guards refuse inline `sk-ant-…`; atomic idempotent claim
  (`signup/key_pool.py`).
- **OTP strength** — CSPRNG, TTL, 5-attempt lockout, send cap, constant-time MAC, Pg-backed
  counters (`signup/tokens.py:174-246`).
- **PKCE correct** (S256, crypto-random verifier+state, fail-closed state check, take-once);
  **no token in any URL**; OAuth `error_description` never rendered (`web/src/auth/`).
- **Ingest shared-token fallback REMOVED** — per-tenant SM names only, fail-loud
  (`ingest/connectors/base.py:40-84`); public endpoints have pre-parse byte caps,
  `extra="forbid"`, field caps; `/public/leads` is a global RLS-exempt sink (no
  cross-tenant targeting); public forms cannot send email.
- **Mock/demo cannot leak into real mode** — build-time flag only; runtime seam removed
  (`web/src/api/client.ts:972-993`).
- **Secrets hygiene (infra)** — all task secrets via `valueFrom`; worker gets only the env
  key (never the org key); provisioning Lambda receives ARNs not values; no secret defaults
  in `variables.tf`; no committed secrets (targeted scans clean).
- **Cortex signed artifacts** — HMAC verified before any deserialization; legacy v1 refused
  on the serving path; `pickle.loads` only in the operator-invoked migration shim
  (`ml/artifacts.py`).
- **Claimed infra hardening confirmed in code** — Aurora deletion-protection/7-day
  backups/private subnets/no-public; Cognito deletion-protection + admin-create-only +
  12-char policy; CloudTrail multi-region + validation + KMS; CloudFront WAF managed rules +
  rate limit, HSTS, https-only viewer+origin; ECS circuit breakers; ECR immutable +
  scan-on-push + lifecycle; SFN/PassRole exactly scoped; GuardDuty + Config present.
