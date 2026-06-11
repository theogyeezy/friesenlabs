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
    "tenant_workspaces", "tenant_settings",
]

# THE static-gate exemption list: tables deliberately OUTSIDE the RLS contract. Every entry is
# PRE-TENANT infrastructure (rows exist before any tenant_id is minted, so a tenant_isolation
# policy cannot apply) and MUST carry an `RLS-EXEMPT` comment in db/schema.sql; access control
# is the crm_app GRANT surface, not RLS. Adding a table here is a deliberate reviewed act.
RLS_EXEMPT_TABLES = [
    "accounts",        # signup rows precede tenant minting (Phase 10)
    "stripe_events",   # webhook idempotency ledger (pre-tenant)
    "workspace_keys",  # pre-minted Anthropic key pool (issue #152 — pre-tenant infrastructure)
    "leads",           # public marketing leads (precede any account or tenant)
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
        # crude but effective: the CREATE TABLE block for t must declare a non-nullable
        # tenant_id uuid (NOT NULL, or PRIMARY KEY which implies it).
        m = re.search(rf"CREATE TABLE IF NOT EXISTS {t} \((.*?)\n\);", sql, re.S)
        assert m, f"no CREATE TABLE found for {t}"
        assert re.search(r"tenant_id\s+uuid\s+(NOT NULL|PRIMARY KEY)", m.group(1)), \
            f"{t} missing a non-nullable 'tenant_id uuid'"


@pytest.mark.unit
def test_every_created_table_is_tenant_scoped_or_explicitly_rls_exempt():
    """The exemption gate: NO table may silently sit outside the RLS contract.

    Every CREATE TABLE in schema.sql must be either in TENANT_TABLES (FORCE'd RLS, asserted
    below) or in the deliberate RLS_EXEMPT_TABLES list above — a new table that is neither
    fails here and forces the author to choose (and document) a side.
    """
    sql = _read(SCHEMA)
    created = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql)
    assert created, "no CREATE TABLE statements found"
    unaccounted = [t for t in created if t not in TENANT_TABLES and t not in RLS_EXEMPT_TABLES]
    assert unaccounted == [], (
        f"tables outside both the RLS contract and the exemption list: {unaccounted} — "
        "add FORCE'd RLS (tenant_tables array + EOF statements) or, for pre-tenant "
        "infrastructure, an RLS-EXEMPT comment + the RLS_EXEMPT_TABLES list here"
    )


@pytest.mark.unit
@pytest.mark.parametrize("table", RLS_EXEMPT_TABLES)
def test_exempt_table_carries_rls_exempt_comment_and_no_policy(table):
    """Every exempt table documents WHY (the RLS-EXEMPT comment convention) — and none of them
    accidentally grows a tenant_isolation policy (which would break pre-tenant writes)."""
    sql = _read(SCHEMA)
    create = sql.find(f"CREATE TABLE IF NOT EXISTS {table} ")
    assert create != -1, f"no CREATE TABLE found for {table}"
    # The RLS-EXEMPT comment sits in the block ABOVE the CREATE (schema convention).
    preceding = sql[max(0, create - 1500):create]
    assert "RLS-EXEMPT" in preceding, \
        f"{table} is exempt but carries no 'RLS-EXEMPT: <reason>' comment block"
    assert not re.search(rf"CREATE POLICY \w+ ON {table}\b", sql), \
        f"{table} is RLS-EXEMPT but has a policy"
    # And it must NOT be in the DO-block tenant_tables array.
    do_block = re.search(r"tenant_tables text\[\] := ARRAY\[(.*?)\];", sql, re.S).group(1)
    assert f"'{table}'" not in do_block, f"{table} is exempt but listed in tenant_tables"


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
