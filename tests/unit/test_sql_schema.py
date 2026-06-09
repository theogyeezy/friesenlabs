"""Unit: the data-plane SQL is grammar-valid and EVERY tenant-scoped table is RLS-FORCE'd.

This is a static gate that runs with no database — it parses the SQL against the real Postgres
grammar (libpg_query via pglast) and asserts the RLS contract, catching the #1 isolation gotcha
("forgot FORCE") automatically.
"""
import os
import re

import pglast
import pytest

DB_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "db")
SCHEMA = os.path.join(DB_DIR, "schema.sql")
ROLES = os.path.join(DB_DIR, "roles.sql")

TENANT_TABLES = [
    "documents", "companies", "contacts", "deals",
    "activities", "saved_views", "approvals", "traces", "ingest_cursor",
]


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.unit
def test_schema_parses():
    """schema.sql is valid Postgres grammar (raises pglast.parser.ParseError otherwise)."""
    stmts = pglast.parse_sql(_read(SCHEMA))
    assert len(stmts) > 0


@pytest.mark.unit
def test_roles_parses():
    stmts = pglast.parse_sql(_read(ROLES))
    assert len(stmts) > 0


@pytest.mark.unit
def test_every_tenant_table_has_tenant_id():
    sql = _read(SCHEMA)
    for t in TENANT_TABLES:
        # crude but effective: the CREATE TABLE block for t must declare tenant_id uuid NOT NULL
        m = re.search(rf"CREATE TABLE IF NOT EXISTS {t} \((.*?)\n\);", sql, re.S)
        assert m, f"no CREATE TABLE found for {t}"
        assert re.search(r"tenant_id\s+uuid\s+NOT NULL", m.group(1)), \
            f"{t} missing 'tenant_id uuid NOT NULL'"


@pytest.mark.unit
@pytest.mark.parametrize("table", TENANT_TABLES)
def test_table_enables_and_forces_rls(table):
    """Without FORCE, the table owner bypasses RLS — tenant isolation silently fails."""
    sql = _read(SCHEMA)
    assert re.search(rf"ALTER TABLE {table}\s+ENABLE ROW LEVEL SECURITY", sql), \
        f"{table} never ENABLEs RLS"
    assert re.search(rf"ALTER TABLE {table}\s+FORCE ROW LEVEL SECURITY", sql), \
        f"{table} never FORCEs RLS (owner would bypass the policy)"


@pytest.mark.unit
def test_policy_uses_app_current_tenant_guc():
    """The policy must key on app.current_tenant (the GUC the app/worker SET per connection).

    The policy is generated inside a DO/format() block, so single quotes are doubled in the
    source. Normalize '' -> ' before matching so we test intent, not escaping.
    """
    sql = _read(SCHEMA).replace("''", "'")
    assert "tenant_isolation" in sql
    assert "current_setting('app.current_tenant'" in sql
    # both USING and WITH CHECK present (read AND write are scoped)
    assert "USING (tenant_id = current_setting('app.current_tenant'" in sql
    assert "WITH CHECK (tenant_id = current_setting('app.current_tenant'" in sql


@pytest.mark.unit
def test_app_role_is_non_bypass():
    """crm_app must be NOBYPASSRLS / NOSUPERUSER or policies no-op."""
    roles = _read(ROLES)
    assert "NOBYPASSRLS" in roles
    assert "NOSUPERUSER" in roles
    assert re.search(r"GRANT SELECT, INSERT, UPDATE, DELETE", roles)
