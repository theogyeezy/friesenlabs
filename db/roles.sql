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
    documents, companies, contacts, deals, activities, saved_views, approvals, traces, ingest_cursor
TO crm_app;

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
