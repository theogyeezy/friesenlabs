# signup/ — Acquisition, Signup & Provisioning (Phase 10)

The self-serve front of the business: account creation + payment + an automated pipeline that spins up
a fully isolated per-tenant instance (incl. a dedicated Anthropic workspace) ONLY after the money
clears. All external systems are injected; live Stripe/Cognito/Anthropic-Admin/Resend/SNS calls are
BLOCKED: needs Nick.

## Flow
1. `accounts.py` — create account (Cognito **unconfirmed**, no tenant_id yet) → verify **email**
   (Resend signed single-use 15-min link) → verify **phone** (SMS OTP). **VERIFY BEFORE PAY.**
2. `payment.py` — Stripe. `start_checkout` refuses until email+phone verified; idempotency key →
   no double-charge. **`handle_webhook` is the ONLY thing that triggers provisioning** — it is
   signature-verified and idempotent (a re-delivered webhook is a no-op). Never the client redirect.
3. `provisioning.py` — the idempotent, rollback-safe pipeline (runs on the paid webhook):
   tenant record + `tenant_id` → Anthropic workspace + scoped key to Secrets Manager → agent plane in
   that workspace → set Cognito `custom:tenant_id` + confirm → Cube context + cost tags + autonomy
   defaults → Resend welcome + flip **active**. Every step is check-then-create; a mid-failure parks
   the account in `provisioning_failed` and rolls back partial resources (no orphan workspace, no
   half-built tenant, no charged customer with no instance).
4. `funnel.py` — PostHog funnel (`landing_view → … → first_login`); revenue captured server-side from
   the webhook so ad-blockers can't drop it.

## The anti-"accidental charge" guarantees (all tested in `tests/unit/test_signup_provisioning.py`)
- verify email + phone before pay · idempotency key (no double-charge) · provision only on the signed
  webhook (bad signature never provisions) · re-delivered webhook never double-provisions · rollback
  on failure parks `provisioning_failed` + tears down the partial workspace · `tenant_id` minted at
  provisioning, never before.

## Not here / needs Nick
Live Stripe keys + webhook secret, Cognito, the Anthropic Admin API (workspace/key endpoints — verify
against current docs), Resend domain (SPF/DKIM/DMARC), SNS/Twilio. The Step Functions orchestration +
the webhook HTTP endpoint are wired in Phase 12 IaC; the logic here is what they call.
