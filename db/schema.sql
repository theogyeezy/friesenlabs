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

-- ===========================================================================
-- ROW LEVEL SECURITY — apply the identical pattern to every tenant-scoped table.
-- The DO block keeps it DRY and guarantees no table is missed (and never without FORCE).
-- ===========================================================================
DO $$
DECLARE
    t text;
    tenant_tables text[] := ARRAY[
        'documents', 'companies', 'contacts', 'deals', 'activities',
        'saved_views', 'approvals', 'traces', 'ingest_cursor'
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
