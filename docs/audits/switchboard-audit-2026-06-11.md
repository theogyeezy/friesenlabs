# Switchboard (integrations module) customer-readiness audit — 2026-06-11

Read-only audit passes (API routes · connector/ingest plane · web real-mode panel + marketing
claims · tests/billing/infra), load-bearing claims spot-checked against source before inclusion.
Branch: `feat/matt-switchboard-audit` (from `main` @ c5162ea). TODOs filed in `TODO.md`
§ "Switchboard customer-readiness audit".

Switchboard = the `integration` module (`shared/modules.py:52`, $29/mo, gates the app route-id
`integrations`). Backend: `api/integrations_routes.py` + `ingest/connectors/*`. Real-mode web:
`web/src/api/IntegrationsPanel.tsx`.

## Verdict

**The code is real and well-built — Switchboard is NOT a Sidecar-style empty SKU — but it is
not customer-ready.** Every endpoint, the connector plane, the honest-state web panel, and 135
local tests are in place and green. What blocks release: (1) in prod every action answers 503
(all go-live switches unset) while the module is sellable; (2) the landing page promises 18+
tools and two-way write-back against a 4-connector read-only backend; (3) a tenant who connects
a credential never gets a recurring sync (operator-listed env, schedule DISABLED); (4) sync runs
synchronously inside the API request; (5) no disconnect, no sync history, and account-delete
leaks vaulted third-party tokens.

## Verified working (evidence-backed)

- **All four endpoints exist and are honest.** `GET /integrations`,
  `POST /integrations/{name}/credentials`, `POST /integrations/{name}/sync`,
  `POST /integrations/csv/import` (`api/integrations_routes.py:352-528`). Unconfigured = 503
  with a reason, never a fake success; unknown connector = 404; csv special cases = 409.
- **THE TRUST RULE holds.** Vault slot is derived ONLY from the verified claim
  (`integrations_routes.py:410-412`); `CredentialsBody` carries the token only — no tenant
  field, smuggled keys ignored (`:328-330`); CSV import threads `claims.tenant_id` only.
- **Secrets hygiene.** Token never logged or echoed (`:416-420`); web input is
  `type="password"`, held transiently, cleared after success (`IntegrationsPanel.tsx:584`).
  Writes via `Boto3SecretWriter` (put-secret-value with create fallback); status via
  DescribeSecret — never reads the value back.
- **No cross-tenant ingestion path from the API.** API-kicked sync requires the tenant's OWN
  vaulted credential, verifiably — the deprecated shared-token fallback is removed from the
  HubSpot connector (`ingest/connectors/hubspot.py:11`) and the route fails closed without a
  per-tenant secret (`integrations_routes.py:437-459`).
- **Connector plane is real.** 4 connectors (hubspot, csv-file, gohighlevel-EXPERIMENTAL,
  stripe-read) with incremental cursors; registry parity between the API mirror and the ingest
  registry is test-pinned (`tests/unit/test_connector_registry.py`); structured rows land in
  the RLS-scoped CRM tables via `PgCrmStructuredSink` (#222), not only `documents`.
- **Web real-mode panel is finished and honest.** Covers all 4 endpoints; maps 503/409/422/502
  to honest copy; never invents success; CSV upload UI with entity picker + mapping override
  (#228/#229). No TODOs, no dead buttons (`web/src/api/IntegrationsPanel.tsx`).
- **Tests green locally: 135 passed** (92 unit across registry/connectors/sink/image-fileset +
  43 routes/secret-writer/csv-import integration), 3 RLS-proof skips that need
  `UPLIFT_TEST_DB_URL` (run in the CI Postgres gate). e2e spec covers the error paths + token
  masking.
- **IAM widened in code.** Connector secret write (api task) and read (ingest task) cover all
  three sources, not just hubspot (`infra/modules/iam/main.tf:253-255`,
  `infra/modules/ingest/main.tf:74`, merged in #235).

## Gaps (release-relevant, all verified)

### P0 — before paying customers

1. **Prod is 100% dark while the SKU is sellable.** `infra/prod.auto.tfvars` carries NONE of:
   `api_integrations_real` (→ `INTEGRATIONS_REAL_SECRETS` unset → credentials POST 503, every
   status "unknown"), `INGEST_*` on the API task (→ sync + CSV import 503 — REQ-004 done-when
   explicitly keeps these off), `ingest_schedule_enabled`/`ingest_tenants` (nightly rule
   applied DISABLED), `module_prices` (→ `STRIPE_PRICE_ID_MODULE_INTEGRATION` unset → the $29
   is never billed). A tenant can enable Switchboard in Settings today and every button 503s.
2. **Marketing overclaims** (`web/src/screens/landing.tsx`): "18+ tools" (lines ~81/164/178),
   a demo carousel of HubSpot/Salesforce/Stripe/Gmail/QuickBooks/Slack (~708) — 4 of 6 don't
   exist; "Keep HubSpot, Salesforce, or Pipedrive" (~223); "Two-way sync & write-back" (~83,
   164) against a backend that is read-only by design ("Uplift never writes back"). The app
   shell also lists Switchboard as "live today" (`web/src/app.tsx:69`).
3. **No recurring sync for connected tenants.** The scheduler syncs only the operator-typed
   `INGEST_TENANTS` env list (`ingest/run_sync.py:198-237`); connecting a credential enrolls
   nobody. Until sync set derives from vaulted credentials (or connected status), "Connect"
   stores a token that nothing ever uses.
4. **Sync is synchronous in-request.** `_build_sync_runner.run` calls `run_one` inline
   (`integrations_routes.py:253-268`) — a large first HubSpot pull will exceed the ALB/request
   budget; no job status, no per-tenant+source concurrency guard (double-click = two
   overlapping syncs racing the cursor).

### P1 — product completeness

5. **No disconnect/revoke.** There is no `DELETE /integrations/{name}/credentials`; once
   vaulted, a token can never be removed by the tenant.
6. **Account-delete leaks connector tokens.** `api/pg_account_delete.py` never touches
   Secrets Manager — `uplift/{tenant}/{source}` slots survive GDPR deletion indefinitely.
7. **No sync history/observability.** No per-run persistence (when/landed/skipped/errors); the
   panel shows only the in-flight response; a customer cannot see "last synced".
8. **Connect never validates the token.** A wrong/revoked token is accepted, vaulted, and
   reported "connected" — it fails only at sync time. No verify-on-connect probe; no OAuth
   (paste-a-token only; the original INT/P2 spec said "start OAuth").
9. **Live VERIFY pass per connector still open** (HubSpot v3 search shapes, GHL v2 headers,
   Stripe params — code-hardened in #224 but never run against live APIs), plus the
   `Boto3SecretWriter` "# VERIFY against live AWS before first prod use" note
   (`integrations_routes.py:163-165`).

### P2 — hygiene / staleness

10. **Stale HOTFIX comment**: `integrations_routes.py:46-54` claims the API image does NOT
    bundle `ingest/` — `api/Dockerfile:21` COPYs it now (the boot-without-ingest regression
    test is still valid as an invariant; the comment isn't).
11. **`shared/modules.py` docstring promises a `web/src/modules.ts` mirror + pinning test —
    neither exists** (the web gate is runtime API-driven via `GET /account/modules`, which is
    fine; fix the docstring or ship the mirror it promises).
12. **`infra/REQUESTS.md` REQ-008 status is stale** (says IAM scoped to `uplift/*/hubspot*`
    only; #235 widened it). Lane Nick: confirm the widened policy is APPLIED live, not just
    merged.
13. **TODO.md "Connectors & ingest" sub-bullets done but unswept**: IAM broadening (#235), csv
    card special-casing + `csvImport` client method + upload UI (#228/#229), PgStructuredSink
    (#222).
