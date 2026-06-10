-- Uplift data plane — schema (Build Guide Phase 1, Steps 10–12).
-- Tenant isolation is born here. EVERY tenant-scoped table:
--   * has tenant_id uuid NOT NULL
--   * ENABLE + FORCE row level security  (FORCE so even the table owner obeys the policy)
--   * a tenant_isolation policy keyed on current_setting('app.current_tenant')
-- The app/worker connects as the NON-OWNER role crm_app (see roles.sql) and sets
-- app.current_tenant per checked-out connection from the verified JWT claim.

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- documents — the vector store (Titan V2, 1024 dims; locked, changing forces re-embed)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id uuid NOT NULL,
    source    text,            -- hubspot|stripe|call|email|upload
    ref_id    text,            -- external id for dedupe / incremental
    content   text,
    embedding vector(1024),    -- Titan Text Embeddings V2
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS documents_embedding_idx ON documents
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 128);
CREATE INDEX IF NOT EXISTS documents_tenant_source_idx ON documents (tenant_id, source);
CREATE UNIQUE INDEX IF NOT EXISTS documents_tenant_ref_idx ON documents (tenant_id, source, ref_id);

-- ---------------------------------------------------------------------------
-- CRM core — system of record
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL,
    name       text NOT NULL,
    domain     text,
    ref_id     text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS contacts (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL,
    company_id uuid REFERENCES companies (id),
    name       text,
    email      text,
    phone      text,
    ref_id     text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS deals (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL,
    company_id  uuid REFERENCES companies (id),
    contact_id  uuid REFERENCES contacts (id),
    title       text,
    stage       text NOT NULL DEFAULT 'new',   -- pipeline stage
    amount      numeric(14, 2),
    currency    text NOT NULL DEFAULT 'USD',
    ref_id      text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS activities (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL,
    contact_id uuid REFERENCES contacts (id),
    deal_id    uuid REFERENCES deals (id),
    kind       text,            -- call|email|note|meeting
    body       text,
    occurred_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- saved_views — dashboard view-specs (Phase 7)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS saved_views (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL,
    view_id       text NOT NULL,
    version       int  NOT NULL DEFAULT 1,
    spec_json     jsonb NOT NULL,
    semantic_refs jsonb,
    source_prompt text,
    created_by    text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- approvals — the Greenlight queue (Phase 5)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS approvals (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    proposed_action jsonb NOT NULL,
    agent           text,
    reasoning       text,
    value_at_stake  numeric(14, 2),
    status          text NOT NULL DEFAULT 'pending',   -- pending|approved|denied|expired
    decided_by      text,
    deny_message    text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    decided_at      timestamptz
);

-- ---------------------------------------------------------------------------
-- traces — per-step agent decision traces (Phase 5)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS traces (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL,
    session_id uuid,
    step       int,
    agent      text,
    kind       text,            -- thought|tool_call|tool_result|message
    payload    jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- ingest_cursor — per-tenant, per-source incremental high-water mark (Phase 2)
-- Tenant-scoped + RLS like everything else (the ingestion worker connects as crm_app).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingest_cursor (
    tenant_id    uuid NOT NULL,
    source       text NOT NULL,
    cursor_value text,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, source)
);

-- ---------------------------------------------------------------------------
-- tenant_workspaces — per-tenant Managed Agents ids (AI plane P0)
-- One row per tenant: the Anthropic workspace / environment / coordinator created at provisioning
-- time, read back by the conversation factory + worker (no per-request roster rebuild).
-- NOTE: declared BEFORE the RLS DO block (like every other tenant table) — the block executes when
-- reached, so any table named in its array must already exist or a fresh load (CI psql
-- ON_ERROR_STOP=1, api/migrate.py's single batch) aborts. Explicit RLS statements are at EOF.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenant_workspaces (
    tenant_id      uuid PRIMARY KEY,
    workspace_id   text,
    environment_id text,
    coordinator_id text,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- accounts — signup/provisioning lifecycle rows (Build Guide Phase 10, Steps 52-55).
-- RLS-EXEMPT (pre-tenant): rows exist before a tenant_id is provisioned; access is restricted to
-- crm_app DML via GRANTs, not RLS
-- A signup row is born with tenant_id NULL (it is minted at provisioning, Step 55), so the
-- tenant_isolation policy cannot apply — this table is deliberately NOT in the tenant_tables
-- array below. The GRANT request lives in infra/REQUESTS.md REQ-002 (db/roles.sql is Lane Nick's).
-- meta jsonb carries account-flow state (cognito_sub, verified flags, stripe_customer_id, the
-- app-level meta dict under 'account') plus the SMS OTP record under 'otp' (signup/store_pg.py
-- merges jsonb atomically so the two writers never clobber each other).
-- NOTE: declared BEFORE the RLS DO block (the block executes when reached; keeping every CREATE
-- TABLE above it preserves the fresh-load ordering contract psql ON_ERROR_STOP=1 relies on).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
    id         uuid PRIMARY KEY,
    email      text UNIQUE,
    phone      text,
    status     text,            -- signup.accounts.State value (created .. active)
    plan       text,
    tenant_id  uuid,            -- NULL until provisioning mints it (pre-tenant by design)
    meta       jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- stripe_events — webhook idempotency ledger (TODO P1: idempotency across restarts/tasks).
-- RLS-EXEMPT (pre-tenant): rows exist before a tenant_id is provisioned; access is restricted to
-- crm_app DML via GRANTs, not RLS
-- Keyed by the Stripe event id: INSERT .. ON CONFLICT (event_id) DO NOTHING is the atomic
-- "claim" — a re-delivered event that fails to insert was already handled by some task.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stripe_events (
    event_id   text PRIMARY KEY,
    account_id uuid,
    handled_at timestamptz NOT NULL DEFAULT now()
);

-- ===========================================================================
-- ROW LEVEL SECURITY — apply the identical pattern to every tenant-scoped table.
-- The DO block keeps it DRY and guarantees no table is missed (and never without FORCE).
-- ===========================================================================
DO $$
DECLARE
    t text;
    tenant_tables text[] := ARRAY[
        'documents', 'companies', 'contacts', 'deals', 'activities',
        'saved_views', 'approvals', 'traces', 'ingest_cursor', 'tenant_workspaces'
    ];
BEGIN
    FOREACH t IN ARRAY tenant_tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE  ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I '
            'USING (tenant_id = current_setting(''app.current_tenant'', true)::uuid) '
            'WITH CHECK (tenant_id = current_setting(''app.current_tenant'', true)::uuid)',
            t
        );
    END LOOP;
END $$;

-- Explicit per-table statements too (belt and suspenders; also documents intent for reviewers
-- and makes the FORCE requirement greppable/testable). These are idempotent with the block above.
ALTER TABLE documents   ENABLE ROW LEVEL SECURITY; ALTER TABLE documents   FORCE ROW LEVEL SECURITY;
ALTER TABLE companies   ENABLE ROW LEVEL SECURITY; ALTER TABLE companies   FORCE ROW LEVEL SECURITY;
ALTER TABLE contacts    ENABLE ROW LEVEL SECURITY; ALTER TABLE contacts    FORCE ROW LEVEL SECURITY;
ALTER TABLE deals       ENABLE ROW LEVEL SECURITY; ALTER TABLE deals       FORCE ROW LEVEL SECURITY;
ALTER TABLE activities  ENABLE ROW LEVEL SECURITY; ALTER TABLE activities  FORCE ROW LEVEL SECURITY;
ALTER TABLE saved_views ENABLE ROW LEVEL SECURITY; ALTER TABLE saved_views FORCE ROW LEVEL SECURITY;
ALTER TABLE approvals   ENABLE ROW LEVEL SECURITY; ALTER TABLE approvals   FORCE ROW LEVEL SECURITY;
ALTER TABLE traces      ENABLE ROW LEVEL SECURITY; ALTER TABLE traces      FORCE ROW LEVEL SECURITY;
ALTER TABLE ingest_cursor ENABLE ROW LEVEL SECURITY; ALTER TABLE ingest_cursor FORCE ROW LEVEL SECURITY;

-- tenant_workspaces — explicit ENABLE/FORCE + policy (belt and suspenders with the DO block above;
-- DROP IF EXISTS + CREATE keeps re-runs idempotent, same as the block's own policy refresh).
ALTER TABLE tenant_workspaces ENABLE ROW LEVEL SECURITY; ALTER TABLE tenant_workspaces FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON tenant_workspaces;
CREATE POLICY tenant_isolation ON tenant_workspaces
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- stripe_events.released_at — claim tombstone (appended per the Matt-append rule).
-- A FAILED webhook attempt RELEASES its claim by setting released_at (signup/store_pg.py
-- PgStripeEventLedger.release) so the event stays retryable; the next claim re-takes the row via
-- INSERT .. ON CONFLICT (event_id) DO UPDATE SET released_at = NULL WHERE released_at IS NOT NULL.
-- A tombstone, NOT a DELETE: the crm_app grant surface on this ledger is append-only
-- (REQ-002 — SELECT/INSERT/UPDATE, no DELETE), and the released row keeps the audit trail.
ALTER TABLE stripe_events ADD COLUMN IF NOT EXISTS released_at timestamptz;
