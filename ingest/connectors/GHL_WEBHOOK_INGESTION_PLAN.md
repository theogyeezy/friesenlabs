# GoHighLevel ingestion — webhook (push) + proxied backfill — build plan/spec (loop-driven)

**Why:** GHL's API (`services.leadconnectorhq.com`) is behind Cloudflare bot protection that BLOCKS
our AWS Fargate egress IP (error 1010 → 403) on outbound *pulls* — confirmed live (identical code +
token works from residential IPs, fails from AWS). So a poll-from-AWS architecture can't be the
ongoing path. The enterprise fix is **hybrid, fully automated, zero human intervention**:

- **Ongoing sync = inbound webhooks** (GHL POSTs events to *us*). Cloudflare never sees our outbound
  call, so the block is irrelevant. This is the steady-state architecture.
- **One-time backfill = a pull routed through a clean, non-AWS egress proxy** (GHL-only, static IP,
  in our control). Keeps backfill automated (no "ask the customer to export a CSV" human step).

This rides rails that ALREADY EXIST: `POST /integrations/csv/import` (`api/integrations_routes.py`)
is the proven inbound-push → land-through-sinks pattern; GHL normalization exists
(`GoHighLevelConnector._contact()/_opportunity()` for typed rows, `gohighlevel_full._normalize()` for
`crm_records`); landing sinks exist (`PgCrmStructuredSink`, `PgCrmRecordsSink`, `PgDocumentStore`).

## Hard guardrails (do not violate)
- **THE TRUST RULE:** the tenant a webhook acts on comes from the VERIFIED per-tenant secret (HMAC /
  signed endpoint token), NEVER from the request body. A forged tenant id in the payload is ignored.
- **No media blobs** (URL refs only). **RLS FORCE + crm_app non-owner.** Never log secrets/PII.
- **Additive only.** Do NOT touch the OAuth login flow (provider config, `/oauth/start|callback`).
- `crm_records` writes MUST set `source="gohighlevel"` (PK is (tenant, source, object_type, ref)).
- Local test runner: `/Users/macpro24gb/dev/.friesen-loop-venv/bin/python -m pytest tests/unit`.
  Do NOT deploy/merge from the loop — open the PR and stop (owner-gated deploy).

## Checklist (work top-to-bottom; each item: implement → test → commit → check box)
- [ ] 1. **Per-tenant webhook secret.** On GHL connect, mint a per-tenant signing secret + a stable
  webhook URL (`/integrations/gohighlevel/webhook?t=<signed-token>` OR HMAC header). Store the secret
  in the vault (`uplift/{tenant}/gohighlevel-webhook`). `shared/config.py` names; reuse the existing
  SecretWriter seam. Unit tests for sign/verify + reject-on-tamper.
- [ ] 2. **Inbound route** `POST /integrations/gohighlevel/webhook` in `api/integrations_routes.py`,
  mounted via `mount_integrations`. Verify the HMAC/signed token → resolve tenant from it (TRUST
  RULE). 401 on bad signature. Gate behind `GHL_WEBHOOKS_ENABLED`. Tests: signature reject, tenant
  bound from secret not body, unknown-tenant 401.
- [ ] 3. **Event normalization.** Map GHL webhook event types (ContactCreate/Update,
  OpportunityCreate/Update/StatusUpdate, +others) to records. Extract reusable normalizers from
  `GoHighLevelConnector._contact()/_opportunity()` and reuse `gohighlevel_full._normalize()`. Tests
  per event type incl. unknown-type tolerance.
- [ ] 4. **Landing (idempotent).** Land through `PgCrmStructuredSink` (typed contacts/deals),
  `PgCrmRecordsSink(source="gohighlevel")` (full-fidelity), and `PgDocumentStore` + Titan embed
  (vector). Idempotent on `(tenant, source, object_type, source_ref_id)` (crm_records) and
  `(tenant, source, ref_id)` (documents) — duplicate deliveries are no-ops. Tests: upsert + dup-delivery.
- [ ] 5. **Backfill via clean egress.** Add an optional `GHL_EGRESS_PROXY` (urllib ProxyHandler) to
  BOTH GHL clients (`gohighlevel.py`, `gohighlevel_full.py`) so the one-time backfill pull leaves
  through a non-AWS static IP. When unset, behaves exactly as today (no change). Unit test: proxy
  wired when env set. Infra (owner): stand up a dedicated GHL-only static-IP proxy; set the env.
- [ ] 6. **Connect UX.** Surface the per-tenant webhook URL + secret in Switchboard so the user (or
  our marketplace-app webhook subscription) can wire GHL → us. Keep the paste-token path working.
- [ ] 7. **Tests green + BUILD_STATUS entry + PR** (owner-gated deploy; don't merge). Note the proxy
  infra + (optional) GHL marketplace webhook-subscription registration as the owner follow-ons.

## Notes
- App-level webhook subscriptions (marketplace app) vs workflow webhooks (per sub-account) — support
  both; workflow webhooks need zero API calls (pure self-serve), app subscriptions scale to many tenants.
- CSV import stays as an EMERGENCY manual fallback only — never the automated product path.
