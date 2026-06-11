# db/ — Uplift data plane schema

`schema.sql` and `roles.sql` define the system-of-record + vector store with **tenant isolation
via Postgres RLS**. They are the runnable Phase 1 deliverable; the Aurora/Redis/S3 infra that hosts
them is in `infra/` (authored + validated, **not applied** — needs Nick).

## Apply order (once the Aurora cluster exists — Nick)
```bash
# 1) as the owner / migration role:
psql "$OWNER_URL" -f db/schema.sql      # tables, pgvector, HNSW index, FORCE'd RLS + policies
psql "$OWNER_URL" -f db/roles.sql       # crm_app non-owner login + grants
psql "$OWNER_URL" -c "ALTER ROLE crm_app PASSWORD '<from Secrets Manager>'"

# 2) the app + worker connect as crm_app (NOT the owner) and scope every connection:
#    SET app.current_tenant = '<tenant uuid from verified JWT>';  ... ; RESET app.current_tenant;
```

## The RLS contract (why isolation holds)
- Every tenant-scoped table is `ENABLE` **and** `FORCE ROW LEVEL SECURITY` (FORCE so even the table
  owner obeys the policy) with a `tenant_isolation` policy keyed on
  `current_setting('app.current_tenant')`.
- The app connects as **`crm_app`** — a plain login role that is `NOSUPERUSER NOBYPASSRLS`. If the
  app connected as the owner or a BYPASSRLS role, policies would silently no-op.
- Vector ANN queries are tenant-scoped too; `hnsw.iterative_scan='relaxed_order'` keeps filtered ANN
  from under-returning.
- **FKs are composite** — Postgres FK checks run as the table owner (they bypass RLS), so the CRM
  child FKs are `(tenant_id, <parent>_id) REFERENCES parent (tenant_id, id)`: a child row can never
  reference another tenant's parent, even with a valid uuid.
- **The audit trail is append-only for the app** — `crm_app` has no DELETE on `approvals`/`traces`
  and no UPDATE on `traces` (approvals keep UPDATE for the Greenlight decided-flip + apply audit).
- **Every tenant table is granted explicitly in roles.sql** — `ALTER DEFAULT PRIVILEGES` only covers
  tables created AFTER it runs, and on a fresh load schema.sql runs first, so a tenant table without
  an explicit GRANT leaves crm_app with zero privileges (the static gate enforces this).

## Tests
- Static (no DB): `pytest tests/unit/test_sql_schema.py` — parses both files with libpg_query and
  asserts FORCE'd RLS + the policy GUC on **every tenant table, with the list DERIVED from
  schema.sql itself** (mandatory `tenant_id` minus explicit `-- RLS-EXEMPT` markers), cross-checked
  against the schema's `tenant_tables` array and the crm_app GRANT surface in roles.sql — the gate
  cannot silently go stale when a table is added. Catches the "forgot FORCE" gotcha.
- Live proof: `pytest tests/integration/test_rls_isolation.py` with `UPLIFT_TEST_DB_URL` (owner) or
  `UPLIFT_DB_URL` (crm_app) set — two-tenant row + vector + update isolation. Skips cleanly with no DB.
- Live tenancy hygiene: `pytest tests/integration/test_tenancy_grants_fk.py` — fresh-load grants on
  `tenant_workspaces`/`tenant_settings`, append-only approvals/traces, cross-tenant FK rejection.
- Cross-cutting gate: `python scripts/isolation_test.py` (run after any data/agent/auth change;
  now also probes the composite same-tenant FKs).
