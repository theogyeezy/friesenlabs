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
- **Status:** OPEN
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
