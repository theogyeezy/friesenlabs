"""Integration: tenancy hygiene against a real Postgres — fresh-load grants, append-only
audit trail, and tenant-scoped referential integrity.

Three contracts proven here (all as the NON-OWNER crm_app role, like prod):

1. FRESH-LOAD GRANTS — `tenant_workspaces` and `tenant_settings` are usable by crm_app from
   roles.sql ALONE. Historically prod only worked by grant-history accident: ALTER DEFAULT
   PRIVILEGES covers tables created AFTER it runs, and on a fresh load schema.sql creates these
   tables BEFORE roles.sql — so without explicit GRANTs crm_app had ZERO privileges on them.
   The fixture simulates the fresh state by REVOKE ALL on those tables and re-running roles.sql,
   so the test passes only if the explicit GRANTs exist (not because of prior grants).

2. APPEND-ONLY AUDIT TRAIL — crm_app can INSERT approvals/traces and UPDATE approvals (the
   Greenlight pending->decided flip + apply audit), but can never DELETE either, and can never
   UPDATE traces.

3. TENANT-SCOPED FKs — FK validation runs with the table owner's rights (it bypasses RLS), so
   the composite (tenant_id, id) FKs must reject a child row referencing ANOTHER tenant's
   parent, while same-tenant references keep working.

Runs only when UPLIFT_TEST_DB_URL points at an owner/superuser DSN (CI provides one); skips
cleanly otherwise.
"""
import os
import urllib.parse as up
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

OWNER_URL = os.environ.get("UPLIFT_TEST_DB_URL")
HERE = os.path.dirname(__file__)
DB_DIR = os.path.join(HERE, "..", "..", "db")


def _load(cur, fname):
    cur.execute(open(os.path.join(DB_DIR, fname)).read())


@pytest.fixture(scope="module")
def app_dsn():
    """Load schema+roles, wipe the two control tables' grant history, re-load roles.sql.

    The re-load is the point: it proves roles.sql ALONE (no historical grants) produces a
    working crm_app surface — the fresh-load scenario api.migrate executes.
    """
    if not OWNER_URL:
        pytest.skip("set UPLIFT_TEST_DB_URL (owner DSN) to run the tenancy grants/FK proof")
    try:
        owner = psycopg2.connect(OWNER_URL)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no reachable Postgres ({e.__class__.__name__})")
    owner.autocommit = True
    with owner.cursor() as cur:
        try:
            _load(cur, "schema.sql")
            _load(cur, "roles.sql")  # ensures crm_app exists before the REVOKE below
            cur.execute(
                "REVOKE ALL ON tenant_workspaces, tenant_settings, approvals, traces FROM crm_app"
            )
            _load(cur, "roles.sql")  # the fresh-load proof: grants must come from THIS file
            cur.execute("ALTER ROLE crm_app PASSWORD 'testpw'")
        except Exception as e:  # noqa: BLE001
            owner.close()
            pytest.skip(f"cannot load schema (needs pgvector + privileges): {e}")
    owner.close()
    parts = up.urlparse(OWNER_URL)
    return up.urlunparse(
        parts._replace(netloc=f"crm_app:testpw@{parts.hostname}:{parts.port or 5432}")
    )


@pytest.fixture()
def app_conn(app_dsn):
    conn = psycopg2.connect(app_dsn)
    yield conn
    conn.rollback()
    conn.close()


def _set_tenant(cur, tenant_id):
    cur.execute("SET app.current_tenant = %s", (str(tenant_id),))


def _denied(cur, sql, params=None):
    """Run a statement that MUST fail with InsufficientPrivilege; keep the txn alive."""
    cur.execute("SAVEPOINT denied_probe")
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute(sql, params or ())
    cur.execute("ROLLBACK TO SAVEPOINT denied_probe")


# --------------------------------------------------------------------------- #
# 1. Fresh-load grants on tenant_workspaces / tenant_settings
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_crm_app_can_use_tenant_workspaces_on_fresh_load(app_conn):
    tenant = str(uuid.uuid4())
    with app_conn.cursor() as cur:
        _set_tenant(cur, tenant)
        # The exact upsert agents/workspace_store.py issues at provisioning time.
        cur.execute(
            "INSERT INTO tenant_workspaces (tenant_id, workspace_id, environment_id, coordinator_id) "
            "VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (tenant_id) DO UPDATE SET workspace_id = EXCLUDED.workspace_id, "
            "environment_id = EXCLUDED.environment_id, coordinator_id = EXCLUDED.coordinator_id",
            (tenant, "ws_1", "env_1", "coord_1"),
        )
        cur.execute("SELECT workspace_id FROM tenant_workspaces WHERE tenant_id = %s", (tenant,))
        assert cur.fetchone()[0] == "ws_1"
        cur.execute(
            "UPDATE tenant_workspaces SET coordinator_id = %s WHERE tenant_id = %s",
            ("coord_2", tenant),
        )
        assert cur.rowcount == 1
        # No DELETE: a tenant's agent-plane ids are re-upserted, never erased by the app.
        _denied(cur, "DELETE FROM tenant_workspaces WHERE tenant_id = %s", (tenant,))
        app_conn.rollback()


@pytest.mark.integration
def test_crm_app_can_use_tenant_settings_on_fresh_load(app_conn):
    tenant = str(uuid.uuid4())
    with app_conn.cursor() as cur:
        _set_tenant(cur, tenant)
        # The exact idempotent seed signup/tenant_defaults.py issues at provisioning step 5.
        cur.execute(
            "INSERT INTO tenant_settings (tenant_id, autonomy_level, cost_tag) "
            "VALUES (%s,%s,%s) ON CONFLICT (tenant_id) DO NOTHING",
            (tenant, "L1", f"tenant:{tenant}"),
        )
        cur.execute("SELECT autonomy_level FROM tenant_settings WHERE tenant_id = %s", (tenant,))
        assert cur.fetchone()[0] == "L1"
        # Operator tuning is an UPDATE (the seed's DO NOTHING never clobbers it).
        cur.execute(
            "UPDATE tenant_settings SET autonomy_level = 'L2' WHERE tenant_id = %s", (tenant,)
        )
        assert cur.rowcount == 1
        _denied(cur, "DELETE FROM tenant_settings WHERE tenant_id = %s", (tenant,))
        app_conn.rollback()


# --------------------------------------------------------------------------- #
# 2. Append-only audit trail (approvals decided-flip allowed; no other mutation)
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_approvals_flip_but_never_delete(app_conn):
    tenant = str(uuid.uuid4())
    with app_conn.cursor() as cur:
        _set_tenant(cur, tenant)
        cur.execute(
            "INSERT INTO approvals (tenant_id, proposed_action, agent, reasoning, status) "
            "VALUES (%s, %s::jsonb, 'sales', 'demo', 'pending') RETURNING id",
            (tenant, '{"action": "send_email"}'),
        )
        approval_id = cur.fetchone()[0]
        # The Greenlight decided-flip (exactly what PgApprovalStore.update issues) must work...
        cur.execute(
            "UPDATE approvals SET status = 'approved', decided_by = 'human', decided_at = now() "
            "WHERE id = %s AND status = 'pending'",
            (approval_id,),
        )
        assert cur.rowcount == 1
        # ...and the post-approval apply audit too.
        cur.execute(
            "UPDATE approvals SET applied_at = now(), apply_result = '{\"ok\": true}'::jsonb "
            "WHERE id = %s",
            (approval_id,),
        )
        assert cur.rowcount == 1
        # But a decision row can never be erased.
        _denied(cur, "DELETE FROM approvals WHERE id = %s", (approval_id,))
        app_conn.rollback()


@pytest.mark.integration
def test_traces_are_strictly_append_only(app_conn):
    tenant = str(uuid.uuid4())
    with app_conn.cursor() as cur:
        _set_tenant(cur, tenant)
        cur.execute(
            "INSERT INTO traces (tenant_id, agent, kind, payload) "
            "VALUES (%s, 'sales', 'tool_call', %s::jsonb) RETURNING id",
            (tenant, '{"tool": "send_email"}'),
        )
        trace_id = cur.fetchone()[0]
        cur.execute("SELECT kind FROM traces WHERE id = %s", (trace_id,))
        assert cur.fetchone()[0] == "tool_call"
        # A decision trace that can be rewritten or removed is not an audit trail.
        _denied(cur, "UPDATE traces SET payload = '{}'::jsonb WHERE id = %s", (trace_id,))
        _denied(cur, "DELETE FROM traces WHERE id = %s", (trace_id,))
        app_conn.rollback()


# --------------------------------------------------------------------------- #
# 3. Composite same-tenant FKs (FK checks bypass RLS — the constraint must carry the tenant)
# --------------------------------------------------------------------------- #
def _fk_rejected(cur, sql, params):
    cur.execute("SAVEPOINT fk_probe")
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        cur.execute(sql, params)
    cur.execute("ROLLBACK TO SAVEPOINT fk_probe")


@pytest.mark.integration
def test_cross_tenant_fk_inserts_fail_same_tenant_succeed(app_conn):
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    with app_conn.cursor() as cur:
        # Tenant A builds a normal same-tenant graph — every legitimate FK still works.
        _set_tenant(cur, tenant_a)
        cur.execute(
            "INSERT INTO companies (tenant_id, name) VALUES (%s, 'A Co') RETURNING id",
            (tenant_a,),
        )
        company_a = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO contacts (tenant_id, company_id, name) VALUES (%s, %s, 'Alice') "
            "RETURNING id",
            (tenant_a, company_a),
        )
        contact_a = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO deals (tenant_id, company_id, contact_id, title) "
            "VALUES (%s, %s, %s, 'A deal') RETURNING id",
            (tenant_a, company_a, contact_a),
        )
        deal_a = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO activities (tenant_id, contact_id, deal_id, kind, body) "
            "VALUES (%s, %s, %s, 'note', 'a-note')",
            (tenant_a, contact_a, deal_a),
        )
        # NULL FK columns stay optional (MATCH SIMPLE — exactly like the old single-column FKs).
        cur.execute(
            "INSERT INTO contacts (tenant_id, company_id, name) VALUES (%s, NULL, 'Solo')",
            (tenant_a,),
        )

        # Tenant B must NOT be able to attach children to tenant A's parents — even though the
        # parent uuids are real (FK lookups bypass RLS; the composite key carries the tenant).
        _set_tenant(cur, tenant_b)
        _fk_rejected(
            cur,
            "INSERT INTO contacts (tenant_id, company_id, name) VALUES (%s, %s, 'Mallory')",
            (tenant_b, company_a),
        )
        _fk_rejected(
            cur,
            "INSERT INTO deals (tenant_id, company_id, title) VALUES (%s, %s, 'steal-co')",
            (tenant_b, company_a),
        )
        _fk_rejected(
            cur,
            "INSERT INTO deals (tenant_id, contact_id, title) VALUES (%s, %s, 'steal-ct')",
            (tenant_b, contact_a),
        )
        _fk_rejected(
            cur,
            "INSERT INTO activities (tenant_id, contact_id, kind, body) "
            "VALUES (%s, %s, 'note', 'x')",
            (tenant_b, contact_a),
        )
        _fk_rejected(
            cur,
            "INSERT INTO activities (tenant_id, deal_id, kind, body) VALUES (%s, %s, 'note', 'x')",
            (tenant_b, deal_a),
        )
        app_conn.rollback()
