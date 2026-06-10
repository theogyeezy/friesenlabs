"""Unit: provisioning tenant-context defaults (TODO INT/P2) — PgTenantDefaults + step 5 wiring.

Proves, with a fake psycopg2 pool (no DB):
  * PgTenantDefaults is a TENANT-scoped store: every operation runs in ONE transaction that
    BEGINS with `SET LOCAL app.current_tenant` (the PgApprovalStore/PgWorkspaceStore pattern —
    tenant_settings is RLS-FORCE'd, unlike the pre-tenant accounts/stripe_events);
  * the seed is IDEMPOTENT — `ON CONFLICT (tenant_id) DO NOTHING`, so SFN step retries are safe
    and can never clobber an operator-tuned autonomy level;
  * the seeded defaults are L1 (matches api/control/autonomy.py AutonomyConfig.default_level)
    + the tenant cost tag;
  * Provisioner step 5 calls the INJECTED tenant_defaults when present and falls back to the
    historic `db` seam when not (offline boots unchanged);
  * api/prod_deps wires PgTenantDefaults only under SIGNUP_REAL_DEPS + a crm_app DSN;
  * the schema: tenant_settings is in the RLS DO-block array AND explicitly ENABLE/FORCE'd.
"""
import os
import re

import pytest

import psycopg2
import psycopg2.pool

import api.prod_deps as prod_deps
from shared.config import Config
from signup.accounts import State
from signup.provisioning import Provisioner
from signup.tenant_defaults import DEFAULT_AUTONOMY_LEVEL, PgTenantDefaults, cost_tag_for

# Reuse the provisioning fakes.
from tests.unit.test_signup_provisioning import (
    AnthropicAdmin, Cognito, DB, Email, Recorder, Secrets, Store,
)
from signup.accounts import AccountService

SCHEMA = os.path.join(os.path.dirname(__file__), "..", "..", "db", "schema.sql")
DSN = "postgresql://crm_app@h/db"
TENANT = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------- fakes (test_signup_store_pg shape)
class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self.rowcount = 0

    def execute(self, sql, params=None):
        self._conn.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._conn.results.pop(0) if self._conn.results else None


class FakeConn:
    def __init__(self):
        self.log: list = []
        self.results: list = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakePool:
    def __init__(self, minconn, maxconn, dsn):
        self.conn = FakeConn()

    def getconn(self):
        return self.conn

    def putconn(self, conn):
        pass


@pytest.fixture
def patched(monkeypatch):
    pool = FakePool(1, 10, None)
    monkeypatch.setattr(
        psycopg2.pool, "ThreadedConnectionPool", lambda minc, maxc, dsn: pool
    )
    return pool


def _cfg(monkeypatch, dsn=None, **overrides):
    monkeypatch.setattr(prod_deps, "load", lambda: Config(**overrides))
    monkeypatch.setattr(prod_deps, "dsn_from_env", lambda: dsn)


# ---------------------------------------------------------------- the SET LOCAL pattern
@pytest.mark.unit
def test_set_tenant_defaults_binds_tenant_first_then_idempotent_insert(patched):
    PgTenantDefaults(DSN).set_tenant_defaults(TENANT)
    (set_sql, set_params), (ins_sql, ins_params) = patched.conn.log
    # The txn BEGINS with the tenant bind — RLS scopes the INSERT (WITH CHECK arm).
    assert set_sql == "SET LOCAL app.current_tenant = %s" and set_params == (TENANT,)
    assert "INSERT INTO tenant_settings" in ins_sql
    assert "ON CONFLICT (tenant_id) DO NOTHING" in ins_sql   # idempotent; never clobbers tuning
    assert ins_params == (TENANT, DEFAULT_AUTONOMY_LEVEL, cost_tag_for(TENANT))
    assert patched.conn.commits == 1


@pytest.mark.unit
def test_seeded_defaults_match_the_runtime_autonomy_default():
    from api.control.autonomy import AutonomyConfig
    assert DEFAULT_AUTONOMY_LEVEL == AutonomyConfig().default_level.value == "L1"
    assert cost_tag_for("t-1") == "tenant:t-1"


@pytest.mark.unit
def test_get_is_tenant_scoped_too(patched):
    patched.conn.results = [{"tenant_id": TENANT, "autonomy_level": "L1"}]
    row = PgTenantDefaults(DSN).get(TENANT)
    assert row == {"tenant_id": TENANT, "autonomy_level": "L1"}
    set_sql, _ = patched.conn.log[0]
    assert set_sql == "SET LOCAL app.current_tenant = %s"


@pytest.mark.unit
def test_error_rolls_back_and_returns_the_connection(patched):
    store = PgTenantDefaults(DSN)

    def boom(sql, params=None):
        raise RuntimeError("db down")

    patched.conn.cursor = lambda cursor_factory=None: type(
        "C", (), {"execute": staticmethod(boom)}
    )()
    with pytest.raises(RuntimeError):
        store.set_tenant_defaults(TENANT)
    assert patched.conn.rollbacks == 1 and patched.conn.commits == 0


# ---------------------------------------------------------------- step 5 wiring
def _paid_account(aid="a1"):
    store = Store()
    svc = AccountService(store, Cognito(), Email(), Recorder())
    svc.create(aid, "u@x.com", "+15555550100")
    svc.verify_email(aid, True)
    svc.verify_phone(aid, True)
    acct = store.get(aid)
    acct.state = State.PAID
    return store, acct


def _provisioner(store, tenant_defaults=None, db=None):
    return Provisioner(
        store=store, mint_tenant_id=lambda aid: f"tenant-{aid}", db=db or DB(),
        anthropic_admin=AnthropicAdmin(), secrets=Secrets(), cognito=Cognito(),
        cube=Recorder(), resend=Recorder(), agent_plane=Recorder(),
        tenant_defaults=tenant_defaults,
    )


@pytest.mark.unit
def test_step5_uses_the_injected_tenant_defaults_over_the_db_fallback():
    store, acct = _paid_account()
    seeded = Recorder()
    db = DB()
    prov = _provisioner(store, tenant_defaults=seeded, db=db)
    assert prov.provision(acct).ok
    assert ("set_tenant_defaults", ("tenant-a1",), {}) in seeded.calls
    # The fallback seam was NOT also called (one writer for the tenant_settings row).
    assert not any(name == "set_tenant_defaults" for (name, _, _) in db.calls)


@pytest.mark.unit
def test_step5_falls_back_to_the_db_seam_when_not_injected():
    store, acct = _paid_account()
    db = DB()
    prov = _provisioner(store, tenant_defaults=None, db=db)
    assert prov.provision(acct).ok
    assert ("set_tenant_defaults", ("tenant-a1",), {}) in db.calls


@pytest.mark.unit
def test_step5_cube_documented_noop_is_still_observable():
    # The cube half of step 5 is a DOCUMENTED no-op in prod (security context is per-request
    # JWT) — but the seam stays observable so tests can veto/observe the step.
    store, acct = _paid_account()
    cube = Recorder()
    prov = Provisioner(
        store=store, mint_tenant_id=lambda aid: f"tenant-{aid}", db=DB(),
        anthropic_admin=AnthropicAdmin(), secrets=Secrets(), cognito=Cognito(),
        cube=cube, resend=Recorder(), agent_plane=Recorder(),
    )
    assert prov.provision(acct).ok
    assert ("ensure_tenant_context", ("tenant-a1",), {}) in cube.calls


# ---------------------------------------------------------------- prod_deps gating
@pytest.mark.unit
def test_prod_deps_wires_pg_tenant_defaults_under_switch_and_dsn(monkeypatch, patched):
    _cfg(monkeypatch, dsn=DSN, signup_real_deps=True)
    prov = prod_deps.build_signup_deps().payment.on_paid.__self__
    assert isinstance(prov.tenant_defaults, PgTenantDefaults)
    # The Lambda cold-start path selects identically.
    assert isinstance(prod_deps.build_provisioner().tenant_defaults, PgTenantDefaults)


@pytest.mark.unit
def test_prod_deps_no_dsn_or_no_switch_keeps_the_noop(monkeypatch):
    import psycopg2.pool as _pool

    def _no_pool(*a, **k):
        raise AssertionError("no Pg pool may be constructed here")

    monkeypatch.setattr(_pool, "ThreadedConnectionPool", _no_pool)
    # Switch on, no DSN -> None (the _Noop db fallback stands).
    _cfg(monkeypatch, signup_real_deps=True)
    assert prod_deps.build_signup_deps().payment.on_paid.__self__.tenant_defaults is None
    # DSN present, switch ABSENT -> deploy invariance: no pool, no seeder.
    _cfg(monkeypatch, dsn=DSN)
    assert prod_deps.build_signup_deps().payment.on_paid.__self__.tenant_defaults is None


# ---------------------------------------------------------------- the schema contract
def _schema() -> str:
    with open(SCHEMA, "r", encoding="utf-8") as f:
        return f.read()


@pytest.mark.unit
def test_tenant_settings_table_shape():
    sql = _schema()
    m = re.search(r"CREATE TABLE IF NOT EXISTS tenant_settings \((.*?)\n\);", sql, re.S)
    assert m, "no CREATE TABLE found for tenant_settings"
    body = m.group(1)
    assert re.search(r"tenant_id\s+uuid\s+PRIMARY KEY", body)
    assert re.search(r"autonomy_level\s+text\s+NOT NULL\s+DEFAULT\s+'L1'", body)
    assert re.search(r"cost_tag\s+text", body)


@pytest.mark.unit
def test_tenant_settings_is_rls_forced_in_block_and_explicitly():
    sql = _schema()
    # In the DO-block array (the DRY guarantee) ...
    block = re.search(r"tenant_tables text\[\] := ARRAY\[(.*?)\];", sql, re.S).group(1)
    assert "'tenant_settings'" in block
    # ... AND the explicit belt-and-suspenders statements (greppable FORCE requirement).
    assert re.search(r"ALTER TABLE tenant_settings\s+ENABLE ROW LEVEL SECURITY", sql)
    assert re.search(r"ALTER TABLE tenant_settings\s+FORCE ROW LEVEL SECURITY", sql)
    assert re.search(
        r"CREATE POLICY tenant_isolation ON tenant_settings\s+"
        r"USING \(tenant_id = current_setting\('app\.current_tenant', true\)::uuid\)\s+"
        r"WITH CHECK \(tenant_id = current_setting\('app\.current_tenant', true\)::uuid\)",
        sql,
    )
    # Declared BEFORE the DO block (fresh-load ordering: psql ON_ERROR_STOP=1 / api.migrate).
    assert sql.index("CREATE TABLE IF NOT EXISTS tenant_settings") < sql.index("DO $$")
