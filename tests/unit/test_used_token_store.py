"""Unit: PgUsedTokenStore (signup/store_pg.py) — Pg-backed single-use email-token nonces.

THE replay finding: api/prod_deps built EmailTokenService with NO used_store, so the single-use
set defaulted to the per-task InMemoryUsedTokenStore — with 2+ Fargate tasks a consumed token
verified AGAIN on the other task for its whole 15-min TTL. These tests (same fake-pool/stub-cursor
patterns as tests/unit/test_signup_store_pg.py — no DB) prove:
  * mark_used is ONE atomic statement (merge computed inside Postgres — no read-modify-write
    window) that prunes expired nonces on write (meta stays bounded) and adds the new one;
  * is_used is a jsonb membership read, honest False on no-row / absent key;
  * accounts stays RLS-EXEMPT pre-tenant here too (no SET LOCAL of any kind);
  * the store plugs straight into EmailTokenService (the UsedTokenStore seam), and replay across
    two SERVICE INSTANCES sharing one store (the 2-task topology) is rejected;
  * api/prod_deps wires PgUsedTokenStore under the dsn guard, the in-memory fallback without.
"""
import pytest

import psycopg2
import psycopg2.pool

import api.prod_deps as prod_deps
from shared.config import Config
from signup.store_pg import PgUsedTokenStore
from signup.tokens import EmailTokenService, InMemoryUsedTokenStore


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self.rowcount = 0

    def execute(self, sql, params=None):
        self._conn.log.append((" ".join(sql.split()), params))
        self.rowcount = self._conn.rowcounts.pop(0) if self._conn.rowcounts else 1

    def fetchone(self):
        return self._conn.results.pop(0) if self._conn.results else None

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self):
        self.log: list = []        # (normalized sql, params) per execute
        self.results: list = []    # queued fetchone() returns
        self.rowcounts: list = []  # queued rowcount per execute
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


def _sql(pool):
    return [s for s, _ in pool.conn.log]


DSN = "postgresql://crm_app@h/db"
ACCT = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------------
# mark_used — one atomic prune-and-add statement
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_mark_used_is_one_atomic_prune_and_add_statement(patched):
    """ONE UPDATE: rebuild the nonce map under the row lock keeping only unexpired entries, then
    merge in the new nonce — never a SELECT-then-UPDATE window, never a flat `meta = %s`."""
    store = PgUsedTokenStore(DSN, now=lambda: 1_000_000.0)
    store.mark_used(ACCT, "nonce-1", 1_000_900)
    assert len(patched.conn.log) == 1                  # exactly one statement
    sql, params = patched.conn.log[0]
    assert "UPDATE accounts SET meta = meta || jsonb_build_object(" in sql
    assert "'used_email_tokens'" in sql
    # The prune: re-aggregate the CURRENT map dropping entries whose expiry has passed.
    assert "jsonb_each(COALESCE(accounts.meta->'used_email_tokens', '{}'::jsonb))" in sql
    assert "WHERE (t.value)::text::bigint >= %s" in sql
    assert "COALESCE(jsonb_object_agg(t.key, t.value), '{}'::jsonb)" in sql
    # The add: the new nonce -> expiry pair merged on top of the pruned map.
    assert "|| jsonb_build_object(%s::text, %s::bigint)" in sql
    assert "updated_at = now()" in sql
    assert params == (1_000_000, "nonce-1", 1_000_900, ACCT)
    assert "meta = %s" not in sql.replace("meta = meta ||", "")   # never a flat replace
    assert not any("app.current_tenant" in s for s in _sql(patched))  # RLS-EXEMPT (pre-tenant)
    assert not any(s.startswith("SET") for s in _sql(patched))


@pytest.mark.unit
def test_mark_used_prune_cutoff_tracks_the_injected_clock(patched):
    """The prune predicate compares against NOW from the injected clock (test seam — the same
    `now` prod_deps threads into the token services)."""
    t = {"now": 2_000_000.0}
    store = PgUsedTokenStore(DSN, now=lambda: t["now"])
    store.mark_used(ACCT, "n1", 2_000_900)
    t["now"] = 2_500_000.0
    store.mark_used(ACCT, "n2", 2_500_900)
    (_, params1), (_, params2) = patched.conn.log
    assert params1[0] == 2_000_000
    assert params2[0] == 2_500_000


# ---------------------------------------------------------------------------
# is_used — jsonb membership read
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_is_used_membership_read_and_honest_misses(patched):
    store = PgUsedTokenStore(DSN)
    patched.conn.results = [{"used": True}, {"used": False}, {"used": None}, None]
    assert store.is_used(ACCT, "seen-nonce") is True
    assert store.is_used(ACCT, "fresh-nonce") is False
    assert store.is_used(ACCT, "no-key-yet") is False   # row exists, key absent -> NULL -> False
    assert store.is_used("22222222-2222-2222-2222-222222222222", "x") is False  # no row
    sql, params = patched.conn.log[0]
    assert "SELECT (meta->'used_email_tokens') ? %s AS used FROM accounts WHERE id = %s" in sql
    assert params == ("seen-nonce", ACCT)
    assert not any("app.current_tenant" in s for s in _sql(patched))  # RLS-EXEMPT (pre-tenant)


# ---------------------------------------------------------------------------
# The EmailTokenService seam (protocol plug + the cross-task replay this fixes)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_store_satisfies_email_token_service_seam(patched):
    """PgUsedTokenStore plugs straight into EmailTokenService: a fresh verify reads the used-set
    (miss) then writes the consumed nonce via the atomic merge."""
    store = PgUsedTokenStore(DSN, now=lambda: 1_000_000.0)
    svc = EmailTokenService("s3cret", used_store=store, now=lambda: 1_000_000.0)
    token = svc.issue(ACCT)
    patched.conn.results = [{"used": False}]            # is_used miss -> consume
    assert svc.verify(ACCT, token) is True
    reads = [s for s in _sql(patched) if s.startswith("SELECT")]
    writes = [s for s in _sql(patched) if s.startswith("UPDATE")]
    assert len(reads) == 1 and "?" in reads[0]
    assert len(writes) == 1 and "'used_email_tokens'" in writes[0]
    # The persisted expiry is the token's own (prune-safety: dropped only once it can't verify).
    _, params = patched.conn.log[1]
    assert params[2] == 1_000_000 + 900                 # default 15-min TTL


@pytest.mark.unit
def test_replay_across_two_service_instances_is_rejected(patched):
    """The 2-Fargate-task topology: TWO EmailTokenService instances (one per task) sharing the
    Pg-backed store. A token consumed on task A must NOT verify on task B."""
    clock = lambda: 1_000_000.0  # noqa: E731
    store = PgUsedTokenStore(DSN, now=clock)
    task_a = EmailTokenService("s3cret", used_store=store, now=clock)
    task_b = EmailTokenService("s3cret", used_store=store, now=clock)
    token = task_a.issue(ACCT)
    patched.conn.results = [{"used": False}]            # task A: fresh -> consumed
    assert task_a.verify(ACCT, token) is True
    patched.conn.results = [{"used": True}]             # task B: the shared store has the nonce
    assert task_b.verify(ACCT, token) is False
    # Exactly one consume write happened (the replay never reached mark_used).
    assert len([s for s in _sql(patched) if s.startswith("UPDATE")]) == 1


@pytest.mark.unit
def test_in_memory_store_keeps_protocol_parity_and_prunes():
    """The dev/test fallback honors the same account-scoped signature, still rejects replay, and
    drops expired nonces on write (same bounded-growth semantics as the Pg prune-on-write)."""
    t = {"now": 1_000_000.0}
    store = InMemoryUsedTokenStore(now=lambda: t["now"])
    store.mark_used("acct-1", "n1", 1_000_900)
    assert store.is_used("acct-1", "n1") is True
    assert store.is_used("acct-2", "n1") is True        # nonce-keyed: nonces are globally unique
    t["now"] = 1_001_000.0                              # past n1's expiry
    store.mark_used("acct-1", "n2", 1_001_900)          # a write prunes the stale nonce
    assert "n1" not in store._used and "n2" in store._used


@pytest.mark.unit
def test_tx_rolls_back_on_error(patched, monkeypatch):
    store = PgUsedTokenStore(DSN)

    def boom(self, sql, params=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(FakeCursor, "execute", boom)
    with pytest.raises(RuntimeError):
        store.mark_used(ACCT, "n", 1)
    assert patched.conn.rollbacks == 1
    assert patched.conn.commits == 0


# ---------------------------------------------------------------------------
# prod_deps wiring (the fix itself: the dsn guard now covers the used-token store)
# ---------------------------------------------------------------------------

def _cfg(monkeypatch, dsn=None, **overrides):
    monkeypatch.setattr(prod_deps, "load", lambda: Config(**overrides))
    monkeypatch.setattr(prod_deps, "dsn_from_env", lambda: dsn)


@pytest.mark.unit
def test_prod_deps_wires_pg_used_token_store_under_dsn_guard(monkeypatch, patched):
    """Under SIGNUP_REAL_DEPS + a DSN, EmailTokenService's single-use set is the SHARED
    PgUsedTokenStore — the consumed-token state survives restarts and spans both Fargate tasks
    (no more per-task replay window)."""
    _cfg(monkeypatch, dsn=DSN, signup_real_deps=True, signup_token_secret_value="sssh")
    deps = prod_deps.build_signup_deps()
    email_tokens = deps.email_token_ok.__self__          # the bound EmailTokenService
    assert isinstance(email_tokens._used, PgUsedTokenStore)


@pytest.mark.unit
def test_prod_deps_without_dsn_falls_back_to_in_memory(monkeypatch):
    """No DSN -> the in-process default (unchanged dev/unconfigured behavior)."""
    _cfg(monkeypatch, signup_real_deps=True, signup_token_secret_value="sssh")
    deps = prod_deps.build_signup_deps()
    email_tokens = deps.email_token_ok.__self__
    assert isinstance(email_tokens._used, InMemoryUsedTokenStore)
