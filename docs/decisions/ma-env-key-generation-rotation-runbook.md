<!-- Decision brief — produced by the QA+DECISIONS lane (2026-06-10).
     Research: parallel agents over repo code + current Anthropic docs; claims adversarially
     spot-checked by an independent critic agent. Status: DRAFT until ratified by Nick + Matt. -->

# MA environment-key generation + rotation runbook

**TL;DR:** The TODO is right and current: live Anthropic docs say verbatim *"Key generation is Console-only, regardless of whether you created the environment through the Console or the API."* There is no API/SDK/`ant` mint or rotate endpoint as of 2026-06-10. The key has **no documented expiry**, Anthropic **cannot fast-revoke a leaked one**, and the docs are **silent on whether two keys can be active at once** — that must be tested empirically on the first rotation. Adopt the 90-day manual runbook below (it reuses the crm-app-db rotate-then-roll pattern already proven in `infra/RUNBOOK.md`), and flag the real strategic problem: per-tenant environments (which `signup/provisioning.py` already creates) each need their own Console click, which breaks self-serve provisioning.

## Context (what the code does today)

- **The question:** `TODO.md:315` — "the env key (sk-ant-oat01-…) is Console-only even when the environment is API-created. Is there an operational runbook?" Note `TODO.md:121` contradicts itself ("the environment key … is produced by `create_environment`") — that wording is **wrong**; `TODO.md:112` and the live docs confirm `create_environment` returns only the env id (`env_012JvqRKUZzUDeH3Gse6TBgZ`, already in `uplift/env-id`), never a key. Fix line 121 when this lands.
- **Secret container exists, empty, no rotation:** `infra/modules/secrets/main.tf:28-31` creates `uplift/env-key` ("Managed Agents ENVIRONMENT key — authenticates the worker to the queue (NOT the org API key)"). It has no `aws_secretsmanager_secret_rotation` — only `crm-app-db` rotates, via the SAR Lambda at lines 121-145. There is no Lambda that *could* rotate this key: rotation requires a human in the Anthropic Console.
- **Worker wiring (REQ-001, DONE @6d5a210):** `infra/modules/worker/main.tf:81-88` injects `UPLIFT_ENV_KEY` via `valueFrom = var.env_key_secret_arn` plus `UPLIFT_ENV_ID`; the comment enforces the security asymmetry — the org `ANTHROPIC_API_KEY` must never appear on the worker task (it lives on the API task only, `infra/REQUESTS.md:74-79`). Module gated on `var.worker_deployed` (`infra/main.tf`, module "worker" block). Service: `desired_count=1`, deployment circuit breaker with rollback, `minimum_healthy_percent=100 / maximum_percent=200` (`worker/main.tf:117-130`) — a roll starts the new task **alongside** the old.
- **Key read once at process start:** `worker/worker.py:129-130` does `env_key = os.environ[ENV_UPLIFT_ENV_KEY]` and passes it as both `auth_token` on `AsyncAnthropic` and `environment_key` on `EnvironmentWorker` (lines 150-161). ECS injects Secrets Manager values at **task launch**, so a rotated secret value takes effect only after a `force-new-deployment` — identical to the crm-app-db pattern in `infra/RUNBOOK.md:142-152`.
- **Detection already built:** `worker/worker.py:37-51,138-142` emits `Uplift/Agents:workers_polling=1` per poll iteration; `infra/modules/observability/main.tf:93-98` alarms (`worker_absent`) when it flatlines, wired to the confirmed SNS email.
- **Current state:** worker authored + validate-clean, **not applied** — parked on exactly this key (Console click) plus the Matt cost call (`TODO.md:159`, `infra/RUNBOOK.md:137-140`, `shared/config.py:21`).
- **The lurking multi-tenant problem:** `signup/provisioning.py:255-276` provisions a **per-tenant** workspace (Admin API) and a **per-tenant** environment via `agent_plane.ensure(...)`, persisting `environment_id` per tenant. Every self-hosted environment needs its own Console-generated key. Self-serve signup therefore cannot fully automate the agent plane as designed — a human click sits in the provisioning path at N tenants.

## What the docs actually say (verified 2026-06-10)

From `platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes.md` (live fetch):
- Generation: Console → environment page → **Generate environment key** → `sk-ant-oat01-…`. *"Key generation is Console-only, regardless of whether you created the environment through the Console or the API."* The environments API surface (create/update/delete/archive + work stats/poll/ack/heartbeat/stop) has **no key endpoint**.
- Graceful degradation: *"if no worker is connected, the session stays queued rather than failing."*
- Claude Platform on AWS: self-hosted workers there authenticate with **AWS IAM (SigV4)** or an AWS-Console API key — *not* an environment key; Claude-Console env keys don't work against that endpoint. (The `GET .../work` list endpoint is currently missing there; poll/ack/heartbeat/stop work.)

From `self-hosted-sandboxes-security.md` (live fetch):
- The key is scoped to **one environment's work queue** (poll + post results). Store in a secrets manager, *"rotate it immediately if you suspect exposure."*
- *"Anthropic … cannot instantly invalidate a key. Treat ANTHROPIC_ENVIRONMENT_KEY like a database password."*
- **No expiry/TTL is documented.** Two-key overlap (does "Generate" revoke the prior key?) is **undocumented** — treat as unknown.

## Options

**Option 1 — Rotate-on-exposure only (the docs' literal minimum).** Cost: zero recurring ops. Risk: with no TTL and no fast-revoke, a quietly leaked key is valid indefinitely, and it's an *integrity* credential, not just read — a holder can claim work items and post forged tool results into live tenant sessions. For a multi-tenant CRM that's unacceptable as the steady state. Effort: nothing.

**Option 2 — Scheduled 90-day manual rotation + alarm-backed runbook (below).** Cost: ~10 min/quarter (one Console click + two CLI commands + verification), $0 infra. Risk: if Console "Generate" revokes the old key, there's a ≤2-3 min window where tool calls stall (sessions queue, nothing is lost) — bounded by running rotations in a low-traffic window. Effort: paste the runbook into `infra/RUNBOOK.md`, add a calendar entry, execute once.

**Option 3 — Eliminate the key class: move the worker to Claude Platform on AWS (IAM SigV4 auth).** Removes the Console human from rotation *and* from per-tenant provisioning (IAM roles are Terraform-able). Cost/risk: a provider migration on a beta surface, one work endpoint currently missing there, and it cuts against the just-made "minimize infra, Anthropic-hosted agent plane" decision; the `agents/runtime.py` seam keeps it cheap to do later. Effort: days, not hours. Wrong move this week.

## Recommendation

**Option 2.** Ship this runbook into `infra/RUNBOOK.md` and run it now to unblock the worker (the deploy itself still waits on the Matt cost call).

**Provisioning (first time, Lane Nick / account owner only):**
1. Console: platform.claude.com → workspace → Environments → `uplift-prod` (`env_012JvqRKUZzUDeH3Gse6TBgZ`) → **Generate environment key**. Copy the `sk-ant-oat01-…` once; it never appears via API.
2. `aws secretsmanager put-secret-value --secret-id uplift/env-key --secret-string 'sk-ant-oat01-…'` — CLI put only; never in git/TF state (same handling as the token-signer, RUNBOOK REQ-003 note).
3. Deploy: `worker_deployed=true` + real `worker_image` in `prod.auto.tfvars` → `terraform apply -target=module.worker` (after the cost go-ahead). **Never delete or empty the secret once the task def references it** — `valueFrom` on an empty secret fails task *startup* with ResourceInitializationError (the exact failure REQ-001 gated `api_anthropic_env` against), and the circuit breaker will flap.
4. Verify: `workers_polling ≥ 1` within 60s (CloudWatch `Uplift/Agents`), `worker_absent` alarm clears; cross-check from outside the worker host with the org key: `ant beta:environments:work stats --environment-id env_012JvqRKUZzUDeH3Gse6TBgZ`.

**Rotation (every 90 days + immediately on suspected exposure or offboarding anyone with Console access):**
1. Console: Generate a new key on `uplift-prod`. **First-rotation experiment:** immediately check the running task — if `workers_polling` stays ≥1 and `/ecs/uplift-worker` shows no 401s, two keys overlap and rotations are true-zero-downtime; record the answer in RUNBOOK. If the old key dies on generate, all subsequent rotations go in a low-traffic window.
2. `aws secretsmanager put-secret-value --secret-id uplift/env-key --secret-string '<new key>'`
3. `aws ecs update-service --cluster uplift-cluster --service uplift-worker --force-new-deployment` — min-100%/max-200% starts the new task beside the old; old drains after the new one is RUNNING.
4. Verify `workers_polling` continuity + one authed `/chat` probe that exercises a tool; if overlap is supported, revoke the old key in Console **after** the roll.
5. Rollback: only meaningful if the old key still works — `update-secret-version-stage --move-to-version-id <AWSPREVIOUS>` + another force-new-deployment. If generate revoked it, rollback = generate again (step 1).

**What breaks on silent invalidation** (no TTL is documented, but a key can still die without warning — another Console user regenerates it, workspace changes, beta policy shifts): worker polls start failing 401 → the per-poll heartbeat stops → `worker_absent` fires within its evaluation window → SNS email. User-visible blast radius: `/chat` tool calls stall and sessions queue (Anthropic-side `work.stats.depth` grows, `oldest_queued_at` ages) — per docs nothing *fails*, it waits. If the SDK loop exits on the auth error, ECS restarts the essential container in a crash loop visible in `/ecs/uplift-worker`. Recovery is the rotation runbook, steps 1-4; MTTR ≈ alarm latency + one Console click + ~2 min task start.

**Carry forward (decision, not runbook):** the per-tenant-environment design in `signup/provisioning.py` puts a human Console click inside the self-serve signup path. Before AI onboarding, pick one: (a) collapse to one shared self-hosted environment for all tenants (one key; isolation via session-metadata tenant stamping + RLS, which `worker/worker.py:100-124` already enforces per call), (b) accept manual env-key generation per tenant (kills "pay → provisioned in minutes"), or (c) Option 3. My lean is (a) until a tenant's compliance posture demands a dedicated trust boundary — the security doc itself frames per-trust-boundary environments as the escalation, not the default.

## What would flip this

- **Anthropic ships an env-key mint/rotate API or `ant` command** → automate generation into provisioning and rotation into a scheduled job; check the environments API reference + SDK changelog before each quarterly rotation.
- **First-rotation test shows generate-revokes-old** → rotations move to low-traffic windows permanently and the runbook's step ordering (generate→put→roll as fast as possible) becomes load-bearing.
- **Tenant count grows with per-tenant environments intact** → the Console click becomes a provisioning SLA problem → forces decision (a) or Option 3.
- **Claude Platform on AWS closes its work-endpoint gap / MA goes GA there** → IAM-authenticated workers eliminate the key class and the Console human; revisit Option 3 via the `runtime.py` seam.
- **A key exposure event** → the "cannot fast-revoke" reality makes the 90-day cadence look generous; tighten to 30 days or move to (c).

## Sources

- Live docs (WebFetch 2026-06-10): `https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes.md` (Console-only quote, queue-not-fail behavior, Claude-Platform-on-AWS IAM auth note); `https://platform.claude.com/docs/en/managed-agents/self-hosted-sandboxes-security.md` (key custody/rotation, no fast-revoke, per-trust-boundary guidance).
- claude-api skill (cached 2026-05/06): `shared/managed-agents-self-hosted-sandboxes.md`, `shared/managed-agents-api-reference.md` (environments endpoint table — no key endpoint), `shared/anthropic-cli.md` (no `ant` key command).
- Repo: `/Users/nick/dev/friesenlabs/TODO.md:17-18,112,121,159,315`; `infra/REQUESTS.md:49-81` (REQ-001); `infra/modules/secrets/main.tf:28-31,121-145`; `infra/modules/worker/main.tf:81-88,117-130`; `infra/main.tf` (module "worker", `worker_deployed` gate); `worker/worker.py:37-51,100-161`; `infra/modules/observability/main.tf:85-98`; `infra/RUNBOOK.md:137-152`; `shared/config.py:21`; `signup/provisioning.py:255-276`.

## ⚠️ CRITIC CORRECTIONS (read before ratifying)
- Claim 6's escape hatch is wrong: self-hosted sandboxes are NOT available on Claude Platform on AWS at all. The claude-api skill states it twice ('except self-hosted sandboxes — config:{type:"self_hosted"} is not available here; use cloud' in the Claude Platform on AWS page, and 'Claude Platform on AWS: Not available' in the self-hosted-sandboxes comparison table), and the repo's own agents/runtime_selfhosted.py docstring records the same ('Claude-Platform-on-AWS does not support self_hosted sandboxes'). There is no documented 'self-hosted workers authenticate with AWS IAM SigV4' path. Correction: no documented escape hatch from Console-minted environment keys exists, so per-tenant environments (provisioning.py:255-278) imply a permanent manual Console step per tenant — the 'defer until per-tenant onboarding forces it' leg of the recommendation deferred toward an option that doesn't exist, and the per-tenant-environment architecture itself may need rethinking (e.g. shared environment + metadata isolation) before self-serve onboarding.

## Critic-noted gaps (non-blocking)
- All other claims verified: infra/modules/worker/main.tf:84 injects UPLIFT_ENV_KEY via valueFrom with no ANTHROPIC_API_KEY; uplift/env-key secret has no rotation config (only crm_app_db has a rotation Lambda, secrets/main.tf:136); worker.py:129-130 reads the key once at startup (ECS resolves valueFrom at task launch, so force-new-deployment is required); Console-only generation and no-fast-revoke match the skill's self-hosted security guidance.
- Claim 5's detection story depends on the workers_polling heartbeat, which is broken as currently written (see the workers-polling brief) — the rotation runbook's 'verify workers_polling' step only works after that fix lands.
