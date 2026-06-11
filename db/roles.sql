-- Uplift data plane — application role (Build Guide Phase 1, Step 11).
--
-- THE TWO WAYS RLS SILENTLY FAILS:
--   (1) You forget FORCE          -> the table owner ignores the policy   (handled in schema.sql)
--   (2) The app connects as owner -> a superuser/BYPASSRLS role no-ops policies
-- => the app and the worker MUST connect as crm_app: a plain, NON-OWNING, NON-superuser,
--    NON-BYPASSRLS login role. This file creates it and grants only DML.
--
-- Run schema.sql FIRST (as the owner/migration role), then this file.
-- Set the password out-of-band (Secrets Manager) -- never commit it.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'crm_app') THEN
        CREATE ROLE crm_app LOGIN;          -- password set out-of-band: ALTER ROLE crm_app PASSWORD '...';
    END IF;
END $$;

-- Defense in depth: crm_app must NOT be able to bypass RLS. A plain login role is already
-- NOSUPERUSER + NOBYPASSRLS by default (only superusers bypass RLS), and on managed Postgres
-- (Aurora/RDS) the master is NOT a true SUPERUSER so it cannot set those attributes — so we only
-- assert the ones the master CAN set. (crm_app stays non-superuser, non-bypass.)
ALTER ROLE crm_app NOCREATEDB NOCREATEROLE;

GRANT USAGE ON SCHEMA public TO crm_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    documents, companies, contacts, deals, activities, saved_views, ingest_cursor
TO crm_app;

-- Audit trail (approvals + traces): the app may APPEND and (approvals only) flip a decision,
-- never erase history.
--   approvals: Greenlight needs UPDATE for the pending->approved/denied flip (status, decided_by,
--   deny_message, edited proposed_action) and the post-approval apply audit (applied_at,
--   apply_result) — but a decision row is never deleted by the app.
--   traces:    strictly append-only (INSERT + SELECT). No UPDATE, no DELETE: a decision trace
--   that can be rewritten or removed is not an audit trail.
GRANT SELECT, INSERT, UPDATE ON approvals TO crm_app;
GRANT SELECT, INSERT ON traces TO crm_app;

-- Per-tenant control rows (RLS-FORCEd tenant tables; see schema.sql): tenant_workspaces is
-- merge-upserted at provisioning (agents/workspace_store.py) and read back by the conversation
-- factory + worker; tenant_settings is seeded idempotently at provisioning step 5
-- (signup/tenant_defaults.py) and read/tuned afterwards. EXPLICIT grants are required: the
-- ALTER DEFAULT PRIVILEGES below only covers tables created AFTER it runs, and on a fresh load
-- schema.sql creates these BEFORE roles.sql — without this line crm_app has ZERO privileges on
-- them (prod only worked because the live roles.sql predated the tables). No DELETE: a tenant's
-- agent-plane ids / settings are flipped or re-upserted, never deleted by the app.
GRANT SELECT, INSERT, UPDATE ON tenant_workspaces, tenant_settings TO crm_app;

-- documents.id is an identity column; grant sequence usage for the others' gen_random_uuid is not
-- needed, but future serial columns would be: keep default privileges sane for new objects.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO crm_app;

-- Connection contract (enforced in app/worker code, shown here for reviewers):
--   SET app.current_tenant = '<tenant uuid from verified JWT>';
--   ... tenant queries ...
--   RESET app.current_tenant;            -- or use SET LOCAL inside a transaction
-- For tenant-filtered ANN so results don't under-return (pgvector 0.8.0 iterative scans):
--   SET hnsw.iterative_scan = 'relaxed_order';
--   SET hnsw.max_scan_tuples = 20000;

-- ---------------------------------------------------------------------------
-- Pre-tenant signup tables (REQ-002): RLS-EXEMPT by design — rows exist before
-- a tenant_id is provisioned, so access control is GRANT-based, not RLS.
-- crm_app gets DML *without DELETE*: accounts are parked/flipped (never deleted
-- by the app) and stripe_events is an append-only idempotency ledger.
GRANT SELECT, INSERT, UPDATE ON accounts, stripe_events TO crm_app;
-- The ALTER DEFAULT PRIVILEGES block above hands DELETE to crm_app on any table
-- created later by the migration role — including these two. Revoke it
-- explicitly or the no-DELETE intent is silently superseded.
REVOKE DELETE ON accounts, stripe_events FROM crm_app;

-- ---------------------------------------------------------------------------
-- Append-only audit trail — explicit REVOKEs (idempotent; safe on fresh AND live loads).
-- GRANT is additive, so a live database that ever held the old broad
-- SELECT/INSERT/UPDATE/DELETE grant keeps DELETE until it is revoked here. Re-asserting the
-- REVOKEs makes a roles.sql re-run (api.migrate runs it on every migration) converge the live
-- grant surface to the design above, instead of depending on grant history.
REVOKE DELETE ON approvals, traces FROM crm_app;          -- audit rows are never erased
REVOKE UPDATE ON traces FROM crm_app;                     -- traces are append-only, not editable
REVOKE DELETE ON tenant_workspaces, tenant_settings FROM crm_app;

-- ---------------------------------------------------------------------------
-- playbooks (Agent Studio): a tenant-content table (RLS-FORCEd; see schema.sql) — full DML.
-- EXPLICIT grant required for the same fresh-load reason as tenant_workspaces/tenant_settings:
-- ALTER DEFAULT PRIVILEGES only covers tables created AFTER it runs, and schema.sql runs first.
-- DELETE is deliberate: playbooks are tenant-authored definitions (not audit trail) and the
-- Studio exposes delete; RLS's WITH CHECK/USING scopes every row either way.
GRANT SELECT, INSERT, UPDATE, DELETE ON playbooks TO crm_app;
-- ---------------------------------------------------------------------------
-- Cortex prediction log (predictions — RLS-FORCEd tenant table; see schema.sql): score-time
-- INSERTs + the outcome-backfill UPDATE (retrain job) + SELECT for live-AUC drift. EXPLICIT
-- grant for the same fresh-load reason as tenant_workspaces/tenant_settings (schema.sql runs
-- first, so ALTER DEFAULT PRIVILEGES never covers it). No DELETE: the prediction log is the
-- drift evidence trail — rows resolve, they are never erased by the app.
GRANT SELECT, INSERT, UPDATE ON predictions TO crm_app;
REVOKE DELETE ON predictions FROM crm_app;

-- ---------------------------------------------------------------------------
-- Pre-tenant infrastructure / acquisition tables (RLS-EXEMPT by design; see schema.sql) —
-- rows exist before any tenant_id, so access control is GRANT-based, not RLS. EXPLICIT grants
-- are required for the same fresh-load reason as the other pre-tenant tables: schema.sql creates
-- these BEFORE roles.sql, so the ALTER DEFAULT PRIVILEGES block above (which only covers tables
-- created AFTER it runs) never reaches them — without these lines crm_app has ZERO privileges and
-- a fresh deploy permission-denies key consumption + lead capture.
--   workspace_keys: the pre-minted Anthropic workspace-key POOL. crm_app SELECTs an available row
--     and UPDATEs it to 'consumed' in the atomic claim (signup/key_pool.py), and the loader INSERTs
--     pool rows (scripts/ops/load_workspace_keys.py). NO DELETE: consumed rows are the
--     key-allocation audit trail — they are never erased by the app.
--   leads: public marketing-site lead capture. crm_app INSERTs a validated row per submission
--     (signup/leads.py) and SELECTs for ops read-back. NO DELETE/UPDATE: a captured lead is an
--     append-only record, never edited or erased by the app.
GRANT SELECT, INSERT, UPDATE ON workspace_keys TO crm_app;
GRANT SELECT, INSERT ON leads TO crm_app;
-- The ALTER DEFAULT PRIVILEGES block above hands DELETE (and, for leads, UPDATE) to crm_app on any
-- table created later by the migration role. Revoke the unintended privileges explicitly, or the
-- no-DELETE intent is silently superseded — and re-asserting them makes a roles.sql re-run
-- (api.migrate runs it on every migration) converge a grant-history live DB to this design.
REVOKE DELETE ON workspace_keys FROM crm_app;
REVOKE UPDATE, DELETE ON leads FROM crm_app;
