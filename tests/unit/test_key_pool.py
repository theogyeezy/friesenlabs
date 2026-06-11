"""Unit: the pre-minted workspace-key pool (signup/key_pool.py — issue #152) + its wiring into
Provisioner step 2 (mocked psycopg2 for the SQL contract; an in-memory pool fake for the
pipeline semantics).

Pins:
  * the pool table stores a Secrets Manager REFERENCE per key, NEVER key material — consume
    returns a PoolKey carrying `secret_ref`, and provisioning resolves it to material via the
    secrets seam (secrets.get) before writing the per-tenant secret (the DB is not the secret
    store);
  * the claim is ONE atomic statement (UPDATE .. WHERE id = (SELECT .. FOR UPDATE SKIP LOCKED)
    RETURNING) and the pool — pre-tenant infrastructure — issues NO SET LOCAL tenant bind;
  * consume is idempotent per tenant (a retry re-reads the SAME row — never burns a 2nd key);
  * an EMPTY pool raises WorkspaceKeyPoolEmpty -> provision() parks the signup in
    provisioning_failed with a pool_empty reason, and the parked signup retries cleanly once
    keys are loaded;
  * a legacy row holding inline key material is REFUSED (the prod guard + consume defense);
  * the low-watermark consume logs the alarms-friendly `workspace_key_pool_low` line;
  * with a pool wired, provisioning NEVER calls the dead Admin-API key-create endpoint (405);
  * the loader insert is idempotent via ON CONFLICT (key_hash) and stores only the reference.
"""
import logging

import pytest

import psycopg2
import psycopg2.pool

from signup.accounts import AccountService, State
from signup.key_pool import (
    InlineKeyMaterialError,
    PgWorkspaceKeyPool,
    PoolKey,
    WorkspaceKeyPoolEmpty,
)
from signup.provisioning import Provisioner

from tests.unit.test_signup_provisioning import (
    AnthropicAdmin, Cognito, DB, Email, Recorder, Secrets, Store,
)
from tests.unit.test_signup_store_pg import FakePool

DSN = "postgresql://crm_app@h/db"
TENANT = "22222222-2222-2222-2222-222222222222"

# A Secrets Manager reference (what the pool table stores) — never key material.
REF1 = "uplift/pool/anthropic_key/abcd1234abcd1234"


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
        {"secret_ref": REF1, "workspace_id": "wrkspc_1", "key_hint": "k1hi"},
        {"n": 10},                                                       # plenty available
    ]
    got = pool.consume(TENANT)
    assert got == PoolKey(secret_ref=REF1, workspace_id="wrkspc_1", key_hint="k1hi")
    sql = _sql(patched)
    claim = next(s for s in sql if s.startswith("UPDATE workspace_keys"))
    assert "FOR UPDATE SKIP LOCKED" in claim          # concurrent provisions never collide/block
    assert "RETURNING" in claim                       # ONE atomic claim statement
    assert "status = 'consumed'" in claim
    # The reference comes back aliased as secret_ref — the column name never leaks into the API.
    assert "AS secret_ref" in claim
    # RLS-EXEMPT pre-tenant infrastructure: no tenant GUC bind of any kind.
    assert not any("app.current_tenant" in s for s in sql)
    assert patched.conn.commits == 1


@pytest.mark.unit
def test_consume_is_idempotent_per_tenant(patched):
    pool = PgWorkspaceKeyPool(DSN)
    patched.conn.results = [
        {"secret_ref": REF1, "workspace_id": "wrkspc_1", "key_hint": "k1hi"},  # prior row
        {"n": 10},
    ]
    got = pool.consume(TENANT)   # the retry path: the SAME reference comes back, no new claim
    assert got.secret_ref == REF1
    assert not any(s.startswith("UPDATE workspace_keys") for s in _sql(patched))


@pytest.mark.unit
def test_consume_empty_pool_raises_pool_empty(patched):
    pool = PgWorkspaceKeyPool(DSN)
    patched.conn.results = [None, None]   # no prior tenant row, no available row
    with pytest.raises(WorkspaceKeyPoolEmpty, match="pool_empty"):
        pool.consume(TENANT)


@pytest.mark.unit
def test_consume_refuses_inline_material_row(patched):
    """Defense in depth: a claimed row whose 'reference' is actually inline key material is a
    legacy plaintext pool — never hand it onward as if it were a reference."""
    pool = PgWorkspaceKeyPool(DSN)
    patched.conn.results = [
        None,
        {"secret_ref": "sk-ant-LEAKED", "workspace_id": None, "key_hint": "AKED"},
        {"n": 10},
    ]
    with pytest.raises(InlineKeyMaterialError):
        pool.consume(TENANT)


@pytest.mark.unit
def test_assert_no_inline_material_passes_clean_pool(patched):
    pool = PgWorkspaceKeyPool(DSN)
    patched.conn.results = [{"n": 0}]   # zero rows hold inline material
    pool.assert_no_inline_material()    # does not raise
    guard = next(s for s in _sql(patched) if "LIKE" in s)
    assert "key_material LIKE" in guard   # scans the ref column for the sk-ant- prefix


@pytest.mark.unit
def test_assert_no_inline_material_refuses_legacy_plaintext_pool(patched):
    pool = PgWorkspaceKeyPool(DSN)
    patched.conn.results = [{"n": 3}]   # 3 rows still hold inline material
    with pytest.raises(InlineKeyMaterialError, match="inline key material"):
        pool.assert_no_inline_material()


@pytest.mark.unit
def test_low_watermark_consume_logs_alarms_friendly_line(patched, caplog):
    pool = PgWorkspaceKeyPool(DSN)
    patched.conn.results = [
        None,
        {"secret_ref": REF1, "workspace_id": None, "key_hint": "k1hi"},
        {"n": 2},   # at/below the default watermark of 3
    ]
    with caplog.at_level(logging.WARNING, logger="signup.key_pool"):
        pool.consume(TENANT)
    line = next(r.getMessage() for r in caplog.records)
    assert "workspace_key_pool_low" in line and "available=2" in line and "low_watermark=3" in line
    # And no secret reference (or material) hits the logs.
    assert REF1 not in line


@pytest.mark.unit
def test_healthy_pool_consume_logs_nothing(patched, caplog):
    pool = PgWorkspaceKeyPool(DSN)
    patched.conn.results = [
        None,
        {"secret_ref": REF1, "workspace_id": None, "key_hint": "k1hi"},
        {"n": 50},
    ]
    with caplog.at_level(logging.WARNING, logger="signup.key_pool"):
        pool.consume(TENANT)
    assert caplog.records == []


@pytest.mark.unit
def test_loader_insert_is_idempotent_on_key_hash_and_stores_reference(patched):
    pool = PgWorkspaceKeyPool(DSN)
    patched.conn.rowcounts = [1, 0]   # first insert lands, the duplicate conflicts away
    inserted = pool.load([
        {"secret_ref": REF1, "key_hash": "h1", "key_hint": "sk-1", "workspace_id": "w1"},
        {"secret_ref": REF1, "key_hash": "h1", "key_hint": "sk-1", "workspace_id": "w1"},
    ])
    assert inserted == 1
    inserts = [(s, p) for s, p in patched.conn.log if s.startswith("INSERT INTO workspace_keys")]
    assert len(inserts) == 2
    assert all("ON CONFLICT (key_hash) DO NOTHING" in s for s, _ in inserts)
    # The inserted value is the reference, never material.
    assert all(REF1 in p for _, p in inserts)


@pytest.mark.unit
def test_loader_refuses_inline_material(patched):
    pool = PgWorkspaceKeyPool(DSN)
    with pytest.raises(InlineKeyMaterialError):
        pool.load([{"secret_ref": "sk-ant-LEAK", "key_hash": "h", "key_hint": "LEAK",
                    "workspace_id": None}])


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
def test_provision_resolves_reference_and_never_calls_admin_key_create():
    svc = AccountService(Store(), Cognito(), Email(), Recorder())
    acct = _verified_paid_account(svc)
    # The dead endpoint (405): with a pool wired it must NEVER be called.
    admin = AnthropicAdmin(fail_on_key=True)
    secrets = Secrets()
    # The pool secret already lives in Secrets Manager (loader wrote it); seed the fake.
    pool_ref = "uplift/pool/anthropic_key/pre1pre1pre1pre1"
    secrets.kv[pool_ref] = "sk-ant-pre1"
    pool = FakeKeyPool([PoolKey(secret_ref=pool_ref, workspace_id="wrkspc_pre1", key_hint="re1")])
    res = _provisioner(svc.store, pool, admin=admin, secrets=secrets).provision(acct)
    assert res.ok
    # Provisioning resolved the reference to material and wrote it to the per-tenant secret.
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
    pool_ref = "uplift/pool/anthropic_key/pre2pre2pre2pre2"
    secrets.kv[pool_ref] = "sk-ant-pre2"
    pool = FakeKeyPool([PoolKey(secret_ref=pool_ref, workspace_id=None, key_hint="re2")])
    res = _provisioner(svc.store, pool, admin=admin, secrets=secrets).provision(acct)
    assert res.ok
    assert secrets.kv["uplift/tenant-a1/anthropic_key"] == "sk-ant-pre2"
    assert "tenant-a1" in admin.workspaces      # idempotent check-then-create still used


@pytest.mark.unit
def test_empty_pool_parks_signup_as_pool_empty_and_retry_recovers():
    svc = AccountService(Store(), Cognito(), Email(), Recorder())
    acct = _verified_paid_account(svc)
    secrets = Secrets()
    pool = FakeKeyPool([])                      # EMPTY
    prov = _provisioner(svc.store, pool, secrets=secrets)
    res = prov.provision(acct)
    assert res.ok is False and res.failed_step == "workspace"
    assert svc.store.get("a1").state is State.PROVISIONING_FAILED
    assert "pool_empty" in acct.meta["provisioning_error"]

    # Double-fire while still empty: idempotent — parked again, no state corruption.
    res2 = prov.provision(acct)
    assert res2.ok is False
    assert svc.store.get("a1").state is State.PROVISIONING_FAILED

    # The owner loads keys -> the standard retry path succeeds with the SAME tenant_id.
    late_ref = "uplift/pool/anthropic_key/late8late8late8la"
    secrets.kv[late_ref] = "sk-ant-late"
    pool.entries.append(PoolKey(secret_ref=late_ref, workspace_id="wrkspc_l8", key_hint="late"))
    retry = prov.retry(acct)
    assert retry["status"] == "ok"
    assert svc.store.get("a1").state is State.ACTIVE
    assert acct.tenant_id == "tenant-a1"
    assert secrets.kv["uplift/tenant-a1/anthropic_key"] == "sk-ant-late"


@pytest.mark.unit
def test_sfn_step_workspace_uses_pool_and_raises_pool_empty_for_retry_policy():
    svc = AccountService(Store(), Cognito(), Email(), Recorder())
    acct = _verified_paid_account(svc)
    secrets = Secrets()
    pool = FakeKeyPool([])
    prov = _provisioner(svc.store, pool, secrets=secrets)
    prov.run_step(acct, "tenant_record")
    with pytest.raises(WorkspaceKeyPoolEmpty):   # SFN Retry/Catch owns the park
        prov.run_step(acct, "workspace")
    # Keys arrive; the re-invoked step is clean and idempotent.
    x_ref = "uplift/pool/anthropic_key/xxxxyyyyxxxxyyyy"
    secrets.kv[x_ref] = "sk-ant-x"
    pool.entries.append(PoolKey(secret_ref=x_ref, workspace_id="wrkspc_x", key_hint="t-x"))
    out = prov.run_step(acct, "workspace")
    assert out["status"] == "ok"
    out2 = prov.run_step(acct, "workspace")      # SFN re-delivery
    assert out2["status"] == "ok"
    assert pool.consumed[acct.tenant_id].secret_ref == x_ref
    assert secrets.kv["uplift/tenant-a1/anthropic_key"] == "sk-ant-x"
