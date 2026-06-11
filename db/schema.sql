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

-- ---------------------------------------------------------------------------
-- tenant_settings — per-tenant defaults seeded at provisioning step 5 (TODO INT/P2).
-- One row per tenant: the default autonomy level (api/control/autonomy.py Level — 'L1' matches
-- AutonomyConfig.default_level) + the tenant's cost-allocation tag. Written by
-- signup/tenant_defaults.py PgTenantDefaults (pooled per-op conn + SET LOCAL in one txn) via an
-- idempotent INSERT .. ON CONFLICT (tenant_id) DO NOTHING — SFN step retries are safe and can
-- never clobber an operator-tuned level. Tenant-scoped + FORCE'd RLS like every other tenant
-- table (it is in the tenant_tables array below).
-- NOTE: declared BEFORE the RLS DO block (the block executes when reached; any table named in
-- its array must already exist or a fresh load — CI psql ON_ERROR_STOP=1, api/migrate.py's
-- single batch — aborts). Explicit RLS statements are at EOF, same as tenant_workspaces.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenant_settings (
    tenant_id      uuid PRIMARY KEY,
    autonomy_level text NOT NULL DEFAULT 'L1',
    cost_tag       text,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- playbooks — Agent Studio playbook definitions (appended per the Matt-append rule).
-- One row per playbook: a named, versioned, declarative definition (jsonb) validated against
-- shared/schemas/playbook.schema.json BEFORE any write (api/routes_studio.py + agents/playbooks)
-- — trigger, roster of owned agents/tools, autonomy level, Greenlight policy. SPEC, NOT CODE:
-- the definition transmits data only, and its greenlight.side_effects field only admits
-- 'always_ask' (draft-only at the schema level). template_id records starter-library
-- provenance; version bumps on every definition update. Tenant-scoped + FORCE'd RLS like
-- every other tenant table (it is in the tenant_tables array below).
-- NOTE: declared BEFORE the RLS DO block (the block executes when reached; any table named in
-- its array must already exist or a fresh load — CI psql ON_ERROR_STOP=1, api/migrate.py's
-- single batch — aborts). Explicit RLS statements are at EOF, same as tenant_settings.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS playbooks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL,
    name        text NOT NULL,
    version     int  NOT NULL DEFAULT 1,
    status      text NOT NULL DEFAULT 'draft',   -- draft|active (agents/playbooks VALID_STATUSES)
    definition  jsonb NOT NULL,
    template_id text,            -- starter-template provenance (NULL = built from scratch)
    created_by  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS playbooks_tenant_idx ON playbooks (tenant_id, created_at);
-- ---------------------------------------------------------------------------
-- predictions — Cortex score-time prediction log (drift honesty; ml/predictions.py, appended
-- per the Matt-append rule). One row per champion-model score; `outcome` stays NULL until the
-- deal closes and the retrain job backfills it (won=1/lost=0) — the resolved (score, outcome)
-- pairs are the REAL input to the live-AUC drift check. Tenant-scoped + FORCE'd RLS like every
-- other tenant table (it is in the tenant_tables array below); written via the same pooled
-- per-op `SET LOCAL app.current_tenant` pattern. No FK on deal_id by design: scores may land
-- before the deal row syncs, and the log must never block on referential timing.
-- NOTE: declared BEFORE the RLS DO block (the block executes when reached; any table named in
-- its array must already exist or a fresh load — CI psql ON_ERROR_STOP=1, api/migrate.py's
-- single batch — aborts). Explicit RLS statements are at EOF, same as tenant_workspaces.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS predictions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL,
    deal_id       uuid,
    model_version int NOT NULL,
    score         double precision NOT NULL,
    features      jsonb,
    outcome       int,             -- NULL until resolved; 1 = won, 0 = lost
    predicted_at  timestamptz NOT NULL DEFAULT now(),
    outcome_at    timestamptz
);
CREATE INDEX IF NOT EXISTS predictions_tenant_predicted_idx ON predictions (tenant_id, predicted_at);
CREATE INDEX IF NOT EXISTS predictions_tenant_deal_open_idx
    ON predictions (tenant_id, deal_id) WHERE outcome IS NULL;

-- ---------------------------------------------------------------------------
-- onboarding_state — per-tenant first-run progress (appended per the Matt-append rule).
-- ONE row per tenant (tenant_id PRIMARY KEY): the dismissible first-run checklist's per-step
-- completion (`steps` jsonb — a flat map of step-id -> bool, NEVER executable content) plus a
-- `dismissed` flag (the tenant skipped/finished the tour) and a `sample_loaded` flag (the
-- one-click "Load sample data" landed the demo fixture into THIS tenant). The route reads/writes
-- it through the same per-op `SET LOCAL app.current_tenant` transaction every tenant store rides,
-- so RLS scopes every read/write; tenant_id NEVER comes from the request body (THE TRUST RULE) —
-- only the verified JWT claim. Tenant-scoped + FORCE'd RLS like every other tenant table (it is in
-- the tenant_tables array above).
-- NOTE: declared BEFORE the RLS DO block (the block executes when reached; any table named in its
-- array must already exist or a fresh load — CI psql ON_ERROR_STOP=1, api/migrate.py's single
-- batch — aborts). Explicit RLS statements are at EOF, same as tenant_settings/playbooks.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS onboarding_state (
    tenant_id     uuid PRIMARY KEY,
    steps         jsonb NOT NULL DEFAULT '{}'::jsonb,  -- step-id -> bool (flat map; data only)
    dismissed     boolean NOT NULL DEFAULT false,      -- tenant skipped/finished the first-run tour
    sample_loaded boolean NOT NULL DEFAULT false,      -- the demo fixture landed in this tenant
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- usage_counters — per-tenant MONTHLY quota counters (appended per the Matt-append rule;
-- idempotent — api.migrate re-runs safely on fresh AND live databases). One row per
-- (tenant_id, period, metric): `period` is the UTC month bucket 'YYYY-MM', `metric` is the
-- counted unit ('messages' | 'agent_actions'). The counter is bumped atomically via
-- INSERT .. ON CONFLICT (tenant_id, period, metric) DO UPDATE SET count = count + EXCLUDED.count
-- so a concurrent bump never loses an increment (the plan-quota gate reads + bumps this).
-- RLS-FORCEd tenant table: tenant_id is mandatory (it anchors the per-op SET LOCAL policy) and
-- the table is in the tenant_tables array below; crm_app gets SELECT/INSERT/UPDATE in roles.sql
-- (the fresh-load grant gap — schema.sql runs before roles.sql). NO DELETE: a usage counter is a
-- billing-period record, rolled by the period bucket, never erased by the app.
-- NOTE: declared BEFORE the RLS DO block (any table named in its tenant_tables array must already
-- exist or a fresh load — CI psql ON_ERROR_STOP=1 — aborts; same ordering as predictions/playbooks).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS usage_counters (
    tenant_id  uuid NOT NULL,
    period     text NOT NULL,          -- UTC month bucket, 'YYYY-MM'
    metric     text NOT NULL,          -- 'messages' | 'agent_actions'
    count      bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, period, metric)
);

-- ---------------------------------------------------------------------------
-- cost_events — per-tenant Anthropic token-usage COST attribution (appended per the Matt-append
-- rule; idempotent). One row per observed agent/MA turn that returned usage: {tenant_id, ts,
-- model, in_tok, out_tok, est_cost}. Append-only MEASUREMENT (never blocks a request) — the
-- per-tenant unit-economics evidence trail (shared/COST.md "per-tenant token logging"). est_cost
-- is the USD estimate computed at write time from shared/cost.py TIER_PRICES (stored so a later
-- price-table change never silently rewrites history). RLS-FORCEd tenant table (mandatory
-- tenant_id; in the tenant_tables array below); crm_app gets SELECT/INSERT in roles.sql. NO
-- UPDATE/DELETE: a recorded cost event is immutable audit, like traces.
-- NOTE: declared BEFORE the RLS DO block, same ordering reason as usage_counters above.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cost_events (
    id         uuid NOT NULL DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL,
    ts         timestamptz NOT NULL DEFAULT now(),
    model      text,
    in_tok     bigint NOT NULL DEFAULT 0,
    out_tok    bigint NOT NULL DEFAULT 0,
    est_cost   numeric(12,6) NOT NULL DEFAULT 0,
    PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS cost_events_tenant_ts_idx ON cost_events (tenant_id, ts);

-- ===========================================================================
-- ROW LEVEL SECURITY — apply the identical pattern to every tenant-scoped table.
-- The DO block keeps it DRY and guarantees no table is missed (and never without FORCE).
-- ===========================================================================
DO $$
DECLARE
    t text;
    tenant_tables text[] := ARRAY[
        'documents', 'companies', 'contacts', 'deals', 'activities',
        'saved_views', 'approvals', 'traces', 'ingest_cursor', 'tenant_workspaces',
        'tenant_settings', 'playbooks', 'predictions', 'usage_counters', 'cost_events', 'onboarding_state'
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

-- tenant_settings — explicit ENABLE/FORCE + policy (belt and suspenders with the DO block above;
-- DROP IF EXISTS + CREATE keeps re-runs idempotent, same as the block's own policy refresh).
ALTER TABLE tenant_settings ENABLE ROW LEVEL SECURITY; ALTER TABLE tenant_settings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON tenant_settings;
CREATE POLICY tenant_isolation ON tenant_settings
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- approvals.apply_* — post-approval execution audit (appended; idempotent).
-- Greenlight decisions remain the source of truth for human approval, while these columns record
-- whether the approved proposal was applied to the CRM or deliberately left record-only.
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS applied_at timestamptz;
ALTER TABLE approvals ADD COLUMN IF NOT EXISTS apply_result jsonb;

-- ---------------------------------------------------------------------------
-- workspace_keys — pre-minted Anthropic workspace-key POOL (appended per the Matt-append rule;
-- issue #152: the Admin API's key-create endpoint 405s — keys are Console-only — so provisioning
-- CONSUMES a pre-minted key from this pool instead of minting one; ratified workspace-ceiling
-- direction on #123. Loader: scripts/ops/load_workspace_keys.py — an owner Console act feeds it).
-- RLS-EXEMPT (pre-tenant infrastructure): pool rows exist BEFORE any tenant_id (consumed_by_tenant
-- is NULL until provisioning claims the row), so the tenant_isolation policy cannot apply — this
-- table is deliberately NOT in the tenant_tables array above. Access is restricted to crm_app DML
-- via GRANTs (SELECT/INSERT/UPDATE, no DELETE — rows are audit trail), not RLS.
-- Consume is ONE atomic claim (UPDATE .. WHERE id = (SELECT .. FOR UPDATE SKIP LOCKED) RETURNING)
-- and is idempotent per tenant via the partial-unique consumed_by_tenant index: a retried
-- provisioning step re-reads the SAME row instead of burning a second key.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workspace_keys (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key_material       text NOT NULL,         -- the pre-minted workspace-scoped API key (Console)
    key_hash           text NOT NULL UNIQUE,  -- sha256 hex of key_material (loader dedupe/idempotency)
    key_hint           text,                  -- non-secret hint (e.g. last 4 chars) for ops logs
    workspace_id       text,                  -- the Console workspace the key is scoped to (if known)
    status             text NOT NULL DEFAULT 'available',   -- available|consumed
    consumed_by_tenant uuid,                  -- set atomically at claim time by provisioning
    consumed_at        timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS workspace_keys_available_idx
    ON workspace_keys (id) WHERE status = 'available';
CREATE UNIQUE INDEX IF NOT EXISTS workspace_keys_consumed_tenant_idx
    ON workspace_keys (consumed_by_tenant) WHERE consumed_by_tenant IS NOT NULL;

-- ---------------------------------------------------------------------------
-- leads — public marketing-site lead capture (POST /public/leads, api/public_routes.py;
-- appended per the Matt-append rule).
-- RLS-EXEMPT (pre-tenant): a lead precedes any account or tenant — there is no tenant_id to key
-- a policy on, so this table is deliberately NOT in the tenant_tables array above. Access is
-- restricted to crm_app DML via GRANTs (INSERT + SELECT), not RLS. The route validates + caps
-- the payload (1KB) and rate-limits per IP before any row is written.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS leads (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind       text NOT NULL,    -- book_call|email (validated by the route)
    name       text NOT NULL,
    email      text NOT NULL,
    message    text,
    company    text,
    source_ip  text,             -- the requester IP the in-process rate limit keyed on
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS leads_created_idx ON leads (created_at);

-- tenant_settings.killswitch_* — the PERSISTED kill switch (appended per the Matt-append rule;
-- idempotent). Reuses the existing tenant_settings table (no new table): TENANT scope lives on the
-- tenant's own row; GLOBAL scope lives on the reserved all-zeros control row
-- (api/control/settings.py GLOBAL_CONTROL_TENANT — uuid4 minting always sets version/variant bits,
-- so a provisioned tenant can never collide with it). Reads/writes ride the same per-op
-- `SET LOCAL app.current_tenant` pattern as every tenant table (RLS scopes both scopes' rows);
-- crm_app DML arrives via the ALTER DEFAULT PRIVILEGES grant in db/roles.sql (live-proven by
-- signup/tenant_defaults.py writing this table). killswitch_updated_at is ops audit only —
-- policy (who may flip which scope) is enforced at the API boundary (api/routes_control.py).
ALTER TABLE tenant_settings ADD COLUMN IF NOT EXISTS killswitch_engaged boolean NOT NULL DEFAULT false;
ALTER TABLE tenant_settings ADD COLUMN IF NOT EXISTS killswitch_updated_at timestamptz;

-- ---------------------------------------------------------------------------
-- Cross-tenant FK hardening (appended; idempotent — psql ON_ERROR_STOP / api.migrate re-runs
-- safely on fresh AND live databases).
--
-- THE HOLE: the inline single-column FKs (contacts.company_id -> companies(id),
-- deals.company_id/contact_id, activities.contact_id/deal_id) validate against the parent's bare
-- id — and Postgres FK checks run with the table OWNER's rights, so RLS does NOT scope the
-- lookup. A row could therefore pass FK validation by pointing at ANOTHER tenant's parent row.
--
-- THE FIX: make tenant_id part of referential integrity itself. Each parent gets
-- UNIQUE (tenant_id, id) (anchoring composite FKs; trivially satisfied — id alone is already the
-- PK), and each child FK becomes (tenant_id, <parent>_id) REFERENCES parent (tenant_id, id), so a
-- child can only ever reference a parent in the SAME tenant. The default MATCH SIMPLE keeps the
-- nullable child columns optional (a NULL company_id/contact_id/deal_id still passes, exactly
-- like the single-column FKs replaced here). The old single-column FKs are dropped LAST, inside
-- the same DO block (one transaction): on a fresh load the inline FKs are created then replaced;
-- if adding a composite FK fails on a live DB (pre-existing cross-tenant row = real data damage,
-- surface it loudly), the whole block rolls back and the old FKs remain in place.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    -- Parents: UNIQUE (tenant_id, id) so the composite FKs have a key to reference.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'companies_tenant_id_id_key'
                     AND conrelid = 'companies'::regclass) THEN
        ALTER TABLE companies ADD CONSTRAINT companies_tenant_id_id_key UNIQUE (tenant_id, id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'contacts_tenant_id_id_key'
                     AND conrelid = 'contacts'::regclass) THEN
        ALTER TABLE contacts ADD CONSTRAINT contacts_tenant_id_id_key UNIQUE (tenant_id, id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'deals_tenant_id_id_key'
                     AND conrelid = 'deals'::regclass) THEN
        ALTER TABLE deals ADD CONSTRAINT deals_tenant_id_id_key UNIQUE (tenant_id, id);
    END IF;

    -- Children: composite same-tenant FKs.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'contacts_tenant_company_fkey'
                     AND conrelid = 'contacts'::regclass) THEN
        ALTER TABLE contacts ADD CONSTRAINT contacts_tenant_company_fkey
            FOREIGN KEY (tenant_id, company_id) REFERENCES companies (tenant_id, id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'deals_tenant_company_fkey'
                     AND conrelid = 'deals'::regclass) THEN
        ALTER TABLE deals ADD CONSTRAINT deals_tenant_company_fkey
            FOREIGN KEY (tenant_id, company_id) REFERENCES companies (tenant_id, id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'deals_tenant_contact_fkey'
                     AND conrelid = 'deals'::regclass) THEN
        ALTER TABLE deals ADD CONSTRAINT deals_tenant_contact_fkey
            FOREIGN KEY (tenant_id, contact_id) REFERENCES contacts (tenant_id, id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'activities_tenant_contact_fkey'
                     AND conrelid = 'activities'::regclass) THEN
        ALTER TABLE activities ADD CONSTRAINT activities_tenant_contact_fkey
            FOREIGN KEY (tenant_id, contact_id) REFERENCES contacts (tenant_id, id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'activities_tenant_deal_fkey'
                     AND conrelid = 'activities'::regclass) THEN
        ALTER TABLE activities ADD CONSTRAINT activities_tenant_deal_fkey
            FOREIGN KEY (tenant_id, deal_id) REFERENCES deals (tenant_id, id);
    END IF;

    -- Retire the cross-tenant-capable single-column FKs (default psql names from the inline
    -- REFERENCES above). Dropped only after every composite FK exists in this transaction —
    -- there is no window where a child column has no tenant-aware FK. Also closes the existence
    -- oracle: with both FKs in place, the error for "uuid exists in another tenant" differed
    -- from "uuid does not exist".
    ALTER TABLE contacts   DROP CONSTRAINT IF EXISTS contacts_company_id_fkey;
    ALTER TABLE deals      DROP CONSTRAINT IF EXISTS deals_company_id_fkey;
    ALTER TABLE deals      DROP CONSTRAINT IF EXISTS deals_contact_id_fkey;
    ALTER TABLE activities DROP CONSTRAINT IF EXISTS activities_contact_id_fkey;
    ALTER TABLE activities DROP CONSTRAINT IF EXISTS activities_deal_id_fkey;
END $$;

-- playbooks — explicit ENABLE/FORCE + policy (belt and suspenders with the DO block above;
-- DROP IF EXISTS + CREATE keeps re-runs idempotent, same as the block's own policy refresh).
ALTER TABLE playbooks ENABLE ROW LEVEL SECURITY; ALTER TABLE playbooks FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON playbooks;
CREATE POLICY tenant_isolation ON playbooks
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- ---------------------------------------------------------------------------
-- predictions — explicit RLS statements (appended; belt and suspenders with the DO block above,
-- same convention as tenant_workspaces/tenant_settings: the FORCE requirement stays greppable).
-- ---------------------------------------------------------------------------
ALTER TABLE predictions ENABLE ROW LEVEL SECURITY; ALTER TABLE predictions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON predictions;
CREATE POLICY tenant_isolation ON predictions
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- ---------------------------------------------------------------------------
-- support_requests — public contact/help intake (POST /public/support, api/support_routes.py;
-- appended per the Matt-append rule).
-- RLS-EXEMPT (pre-tenant): a support request precedes — or outlives — any tenant binding (a
-- prospect with a question, a locked-out customer), so there is no trustworthy tenant_id to key a
-- policy on. This table is deliberately NOT in the tenant_tables array above. `tenant_hint` is a
-- FREE-TEXT triage hint a user types ("I think my workspace is acme") — it is NEVER trusted for
-- authorization, never resolved to a real tenant_id, and never used to bind RLS (THE TRUST RULE).
-- Access is restricted to crm_app DML via GRANTs (INSERT + SELECT), not RLS. The route validates +
-- caps the payload (2KB) and rate-limits per IP before any row is written. The crm_app GRANT lives
-- in infra/REQUESTS.md (db/roles.sql is Lane Nick's) — see the REQ block this PR appends.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS support_requests (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    email       text NOT NULL,
    subject     text NOT NULL,
    message     text NOT NULL,
    tenant_hint text,             -- free-text workspace hint; NEVER trusted for auth/RLS
    source_ip   text,             -- the requester IP the in-process rate limit keyed on
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS support_requests_created_idx ON support_requests (created_at);

-- usage_counters + cost_events — explicit ENABLE/FORCE + policy (belt and suspenders with the DO
-- block above; the CREATE TABLEs live BEFORE the block — see the predictions/playbooks precedent —
-- because any table named in the block's tenant_tables array must already exist on a fresh load).
ALTER TABLE usage_counters ENABLE ROW LEVEL SECURITY; ALTER TABLE usage_counters FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON usage_counters;
CREATE POLICY tenant_isolation ON usage_counters
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);

ALTER TABLE cost_events ENABLE ROW LEVEL SECURITY; ALTER TABLE cost_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON cost_events;
CREATE POLICY tenant_isolation ON cost_events
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- ---------------------------------------------------------------------------
-- onboarding_state — explicit RLS statements (appended; belt and suspenders with the DO block
-- above, same convention as tenant_settings/playbooks: the FORCE requirement stays greppable).
-- ---------------------------------------------------------------------------
ALTER TABLE onboarding_state ENABLE ROW LEVEL SECURITY; ALTER TABLE onboarding_state FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON onboarding_state;
CREATE POLICY tenant_isolation ON onboarding_state
    USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);

-- tenant_settings.workspace_name / notification_prefs — the PERSISTED workspace-settings surface
-- (appended per the Matt-append rule; idempotent). Reuses the existing tenant_settings table (no
-- new table, no new policy): the row is already TENANT-scoped + FORCE'd RLS with a tenant_isolation
-- policy (autonomy_level/killswitch live here too), so ADDING columns inherits that scoping. Backs
-- GET/PUT /account/settings (api/settings_routes.py + api/pg_settings.py PgSettingsStore): the
-- workspace's display name + a flat jsonb bag of notification preferences. Reads/writes ride the
-- same per-op `SET LOCAL app.current_tenant` pattern as every tenant table; crm_app DML arrives via
-- the ALTER DEFAULT PRIVILEGES grant in db/roles.sql (already proven live for this table by the
-- kill-switch upsert). A settings upsert is an explicit user action and DO UPDATEs (it MUST win
-- over the provisioning-seeded DO NOTHING row), same as the kill switch / autonomy dial.
ALTER TABLE tenant_settings ADD COLUMN IF NOT EXISTS workspace_name text;
ALTER TABLE tenant_settings ADD COLUMN IF NOT EXISTS notification_prefs jsonb NOT NULL DEFAULT '{}'::jsonb;
-- tenant_settings.enabled_modules — the per-tenant MODULE ENTITLEMENTS (shared/modules.py catalog).
-- A jsonb array of enabled module ids; the app shows only these modules' routes + the always-on
-- ones. Empty/absent => the store falls back to shared.modules.default_enabled() (the required
-- spine). Same tenant-scoped FORCE'd RLS + SET LOCAL discipline as the columns above. Backs
-- GET/PUT /account/modules (api/modules_routes.py + PgSettingsStore.get_modules/set_modules); each
-- enabled module is a Stripe subscription item in the Phase-2 "selection sets the price" billing.
ALTER TABLE tenant_settings ADD COLUMN IF NOT EXISTS enabled_modules jsonb NOT NULL DEFAULT '[]'::jsonb;
