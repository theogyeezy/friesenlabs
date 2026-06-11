"""Unit: the pre-minted workspace-key pool (signup/key_pool.py — issue #152) + its wiring into
Provisioner step 2 (mocked psycopg2 for the SQL contract; an in-memory pool fake for the
pipeline semantics).

Pins:
  * the claim is ONE atomic statement (UPDATE .. WHERE id = (SELECT .. FOR UPDATE SKIP LOCKED)
    RETURNING) and the pool — pre-tenant infrastructure — issues NO SET LOCAL tenant bind;
  * consume is idempotent per tenant (a retry re-reads the SAME row — never burns a 2nd key);
  * an EMPTY pool raises WorkspaceKeyPoolEmpty -> provision() parks the signup in
    provisioning_failed with a pool_empty reason, and the parked signup retries cleanly once
    keys are loaded;
  * the low-watermark consume logs the alarms-friendly `workspace_key_pool_low` line;
  * with a pool wired, provisioning NEVER calls the dead Admin-API key-create endpoint (405);
  * the loader insert is idempotent via ON CONFLICT (key_hash).
"""
import logging

import pytest

import psycopg2
import psycopg2.pool

from signup.accounts import AccountService, State
from signup.key_pool import PgWorkspaceKeyPool, PoolKey, WorkspaceKeyPoolEmpty
from signup.provisioning import Provisioner

from tests.unit.test_signup_provisioning import (
    AnthropicAdmin, Cognito, DB, Email, Recorder, Secrets, Store,
)
from tests.unit.test_signup_store_pg import FakePool

DSN = "postgresql://crm_app@h/db"
TENANT = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def patched(monkeypatch):
    pool = FakePool(1, 10, None)
    monkeypatch.setattr(
        psycopg2.pool, "ThreadedConnectionPool", lambda minc, maxc, dsn: pool
    )
    return pool


def _sql(pool):
    return [s for s, _ in pool.conn.log]


# ---------------------------------------------------------------- the Pg SQL contract
@pytest.mark.unit
def test_consume_claims_atomically_skip_locked_no_tenant_bind(patched):
    pool = PgWorkspaceKeyPool(DSN)
    patched.conn.results = [
        None,                                                            # no prior tenant row
        {"key_material": "sk-ant-k1", "workspace_id": "wrkspc_1", "key_hint": "k1hi"},
        {"n": 10},                                                       # plenty available
    ]
    got = pool.consume(TENANT)
    assert got == PoolKey(key="sk-ant-k1", workspace_id="wrkspc_1", key_hint="k1hi")
    sql = _sql(patched)
    claim = next(s for s in sql if s.startswith("UPDATE workspace_keys"))
    assert "FOR UPDATE SKIP LOCKED" in claim          # concurrent provisions never collide/block
    assert "RETURNING" in claim                       # ONE atomic claim statement
    assert "status = 'consumed'" in claim
    # RLS-EXEMPT pre-tenant infrastructure: no tenant GUC bind of any kind.
    assert not any("app.current_tenant" in s for s in sql)
    assert patched.conn.commits == 1


@pytest.mark.unit
def test_consume_is_idempotent_per_tenant(patched):
    pool = PgWorkspaceKeyPool(DSN)
    patched.conn.results = [
        {"key_material": "sk-ant-k1", "workspace_id": "wrkspc_1", "key_hint": "k1hi"},  # prior row
        {"n": 10},
    ]
    got = pool.consume(TENANT)   # the retry path: the SAME key comes back, no new claim
    assert got.key == "sk-ant-k1"
    assert not any(s.startswith("UPDATE workspace_keys") for s in _sql(patched))


@pytest.mark.unit
def test_consume_empty_pool_raises_pool_empty(patched):
    pool = PgWorkspaceKeyPool(DSN)
    patched.conn.results = [None, None]   # no prior tenant row, no available row
    with pytest.raises(WorkspaceKeyPoolEmpty, match="pool_empty"):
        pool.consume(TENANT)


@pytest.mark.unit
def test_low_watermark_consume_logs_alarms_friendly_line(patched, caplog):
    pool = PgWorkspaceKeyPool(DSN)
    patched.conn.results = [
        None,
        {"key_material": "sk-ant-k1", "workspace_id": None, "key_hint": "k1hi"},
        {"n": 2},   # at/below the default watermark of 3
    ]
    with caplog.at_level(logging.WARNING, logger="signup.key_pool"):
        pool.consume(TENANT)
    line = next(r.getMessage() for r in caplog.records)
    assert "workspace_key_pool_low" in line and "available=2" in line and "low_watermark=3" in line
    # And the key material itself never hits the logs.
    assert "sk-ant-k1" not in line


@pytest.mark.unit
def test_healthy_pool_consume_logs_nothing(patched, caplog):
    pool = PgWorkspaceKeyPool(DSN)
    patched.conn.results = [
        None,
        {"key_material": "sk-ant-k1", "workspace_id": None, "key_hint": "k1hi"},
        {"n": 50},
    ]
    with caplog.at_level(logging.WARNING, logger="signup.key_pool"):
        pool.consume(TENANT)
    assert caplog.records == []


@pytest.mark.unit
def test_loader_insert_is_idempotent_on_key_hash(patched):
    pool = PgWorkspaceKeyPool(DSN)
    patched.conn.rowcounts = [1, 0]   # first insert lands, the duplicate conflicts away
    inserted = pool.load([
        {"key": "sk-1", "key_hash": "h1", "key_hint": "sk-1", "workspace_id": "w1"},
        {"key": "sk-1", "key_hash": "h1", "key_hint": "sk-1", "workspace_id": "w1"},
    ])
    assert inserted == 1
    inserts = [s for s in _sql(patched) if s.startswith("INSERT INTO workspace_keys")]
    assert len(inserts) == 2
    assert all("ON CONFLICT (key_hash) DO NOTHING" in s for s in inserts)


# ---------------------------------------------------------------- pipeline semantics
class FakeKeyPool:
    """In-memory pool with the exact consume contract (idempotent per tenant, raises on empty)."""

    def __init__(self, entries=None):
        self.entries = list(entries or [])
        self.consumed: dict[str, PoolKey] = {}

    def consume(self, tenant_id):
        if tenant_id in self.consumed:
            return self.consumed[tenant_id]
        if not self.entries:
            raise WorkspaceKeyPoolEmpty("pool_empty: no pre-minted workspace keys available")
        entry = self.entries.pop(0)
        self.consumed[tenant_id] = entry
        return entry


def _verified_paid_account(svc, aid="a1"):
    svc.create(aid, "u@x.com", "+15555550100")
    svc.verify_email(aid, True)
    svc.verify_phone(aid, True)
    acct = svc.store.get(aid)
    acct.state = State.PAID
    return acct


def _provisioner(store, key_pool, admin=None, secrets=None):
    return Provisioner(
        store=store, mint_tenant_id=lambda aid: f"tenant-{aid}", db=DB(),
        anthropic_admin=admin or AnthropicAdmin(), secrets=secrets or Secrets(),
        cognito=Cognito(), cube=Recorder(), resend=Recorder(), agent_plane=Recorder(),
        key_pool=key_pool,
    )


@pytest.mark.unit
def test_provision_consumes_pool_key_and_never_calls_admin_key_create():
    svc = AccountService(Store(), Cognito(), Email(), Recorder())
    acct = _verified_paid_account(svc)
    # The dead endpoint (405): with a pool wired it must NEVER be called.
    admin = AnthropicAdmin(fail_on_key=True)
    secrets = Secrets()
    pool = FakeKeyPool([PoolKey(key="sk-ant-pre1", workspace_id="wrkspc_pre1", key_hint="re1")])
    res = _provisioner(svc.store, pool, admin=admin, secrets=secrets).provision(acct)
    assert res.ok
    assert secrets.kv["uplift/tenant-a1/anthropic_key"] == "sk-ant-pre1"
    assert admin.keys == {}                     # create_workspace_key untouched
    assert admin.workspaces == {}               # the pool's Console workspace was used as-is
    assert pool.consumed["tenant-a1"].workspace_id == "wrkspc_pre1"


@pytest.mark.unit
def test_pool_entry_without_workspace_falls_back_to_ensure_workspace():
    svc = AccountService(Store(), Cognito(), Email(), Recorder())
    acct = _verified_paid_account(svc)
    admin = AnthropicAdmin(fail_on_key=True)
    secrets = Secrets()
    pool = FakeKeyPool([PoolKey(key="sk-ant-pre2", workspace_id=None, key_hint="re2")])
    res = _provisioner(svc.store, pool, admin=admin, secrets=secrets).provision(acct)
    assert res.ok
    assert secrets.kv["uplift/tenant-a1/anthropic_key"] == "sk-ant-pre2"
    assert "tenant-a1" in admin.workspaces      # idempotent check-then-create still used


@pytest.mark.unit
def test_empty_pool_parks_signup_as_pool_empty_and_retry_recovers():
    svc = AccountService(Store(), Cognito(), Email(), Recorder())
    acct = _verified_paid_account(svc)
    pool = FakeKeyPool([])                      # EMPTY
    prov = _provisioner(svc.store, pool)
    res = prov.provision(acct)
    assert res.ok is False and res.failed_step == "workspace"
    assert svc.store.get("a1").state is State.PROVISIONING_FAILED
    assert "pool_empty" in acct.meta["provisioning_error"]

    # Double-fire while still empty: idempotent — parked again, no state corruption.
    res2 = prov.provision(acct)
    assert res2.ok is False
    assert svc.store.get("a1").state is State.PROVISIONING_FAILED

    # The owner loads keys -> the standard retry path succeeds with the SAME tenant_id.
    pool.entries.append(PoolKey(key="sk-ant-late", workspace_id="wrkspc_l8", key_hint="late"))
    retry = prov.retry(acct)
    assert retry["status"] == "ok"
    assert svc.store.get("a1").state is State.ACTIVE
    assert acct.tenant_id == "tenant-a1"


@pytest.mark.unit
def test_sfn_step_workspace_uses_pool_and_raises_pool_empty_for_retry_policy():
    svc = AccountService(Store(), Cognito(), Email(), Recorder())
    acct = _verified_paid_account(svc)
    pool = FakeKeyPool([])
    prov = _provisioner(svc.store, pool)
    prov.run_step(acct, "tenant_record")
    with pytest.raises(WorkspaceKeyPoolEmpty):   # SFN Retry/Catch owns the park
        prov.run_step(acct, "workspace")
    # Keys arrive; the re-invoked step is clean and idempotent.
    pool.entries.append(PoolKey(key="sk-ant-x", workspace_id="wrkspc_x", key_hint="t-x"))
    out = prov.run_step(acct, "workspace")
    assert out["status"] == "ok"
    out2 = prov.run_step(acct, "workspace")      # SFN re-delivery
    assert out2["status"] == "ok"
    assert pool.consumed[acct.tenant_id].key == "sk-ant-x"
