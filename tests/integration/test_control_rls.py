"""Integration: the persisted control plane against REAL Postgres RLS.

Extends the test_rls_isolation.py pattern to the accountability stores:
  * PgTraceStore — tenant A's trace list can NEVER see tenant B's rows (FORCE'd RLS on traces),
    and keyset pagination walks the full set newest-first with no overlap.
  * PgControlSettingsStore — tenant-scoped reads/writes; one tenant's kill switch never bleeds.
  * MULTI-INSTANCE kill switch — two PersistedKillSwitch facades over two SEPARATE store
    instances (two pools == two API tasks): one flips, the other sees it (ttl=0 here; live the
    TTL bounds staleness to ~2s). Global scope pauses every tenant on every instance.
  * The autonomy dial set on one instance is read by the other.

Runs against a real Postgres+pgvector only when reachable (same gating as the siblings):
  - UPLIFT_TEST_DB_URL  -> a superuser/owner URL used to load schema.sql + roles.sql, OR
  - UPLIFT_DB_URL       -> an already-provisioned crm_app URL (skip the load step)
Skips with a clear reason otherwise (green locally; the hard gate runs in CI).
"""
import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from api.control.settings import (  # noqa: E402
    GLOBAL_CONTROL_TENANT,
    PersistedAutonomyDial,
    PersistedKillSwitch,
)
from api.control.traces import PgTraceStore, append_trace  # noqa: E402
from api.control.types import Level  # noqa: E402
from api.pg_clients import PgControlSettingsStore  # noqa: E402

OWNER_URL = os.environ.get("UPLIFT_TEST_DB_URL")
APP_URL = os.environ.get("UPLIFT_DB_URL")
HERE = os.path.dirname(__file__)
DB_DIR = os.path.join(HERE, "..", "..", "db")


def _connect(url):
    try:
        return psycopg2.connect(url)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no reachable Postgres ({e.__class__.__name__})")


@pytest.fixture(scope="module")
def app_dsn():
    if not OWNER_URL and not APP_URL:
        pytest.skip("set UPLIFT_TEST_DB_URL (owner) or UPLIFT_DB_URL (crm_app) to run the control RLS proof")
    if OWNER_URL:
        owner = _connect(OWNER_URL)
        owner.autocommit = True
        with owner.cursor() as cur:
            try:
                cur.execute(open(os.path.join(DB_DIR, "schema.sql")).read())
                cur.execute(open(os.path.join(DB_DIR, "roles.sql")).read())
                cur.execute("ALTER ROLE crm_app PASSWORD 'testpw'")
                # Mirror the LIVE grant state: in prod tenant_settings was created by a later
                # api.migrate run, so roles.sql's ALTER DEFAULT PRIVILEGES handed crm_app DML
                # (REQ-006 note). A FRESH load runs schema.sql before roles.sql, so default
                # privileges never cover it — grant explicitly here (and see the PR note asking
                # Lane Nick to add the explicit grant to db/roles.sql for fresh-load parity).
                cur.execute("GRANT SELECT, INSERT, UPDATE ON tenant_settings TO crm_app")
            except Exception as e:  # noqa: BLE001
                pytest.skip(f"cannot load schema (needs pgvector + privileges): {e}")
        owner.close()
        import urllib.parse as up
        parts = up.urlparse(OWNER_URL)
        return up.urlunparse(
            parts._replace(netloc=f"crm_app:testpw@{parts.hostname}:{parts.port or 5432}"))
    _connect(APP_URL).close()
    return APP_URL


@pytest.fixture
def make_stores(app_dsn, monkeypatch):
    """Build stores with TINY pools and CLOSE them at teardown.

    The fixed-size pool (min == max, default 10) opens every connection at construction and the
    stores never close it — fine for the long-lived asgi singletons, but a test module building
    several 'instances' would exhaust the CI service's connection slots (the live failure mode
    this fixture exists for). 2 connections per pool, all released after each test.
    """
    monkeypatch.setenv("UPLIFT_DB_POOL_MAX", "2")
    pools = []

    def settings() -> PgControlSettingsStore:
        s = PgControlSettingsStore(app_dsn)
        pools.append(s._pool)
        return s

    def traces() -> PgTraceStore:
        t = PgTraceStore(app_dsn)
        pools.append(t._client._pool)
        return t

    yield settings, traces
    for p in pools:
        p.closeall()


@pytest.mark.integration
def test_traces_rls_isolation_and_pagination(make_stores):
    _, make_traces = make_stores
    store = make_traces()
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    for i in range(5):
        append_trace(store, tenant_id=a, agent="nadia", tool=f"tool-{i}", kind="executed",
                     inputs={"i": i}, reasoning=f"a-reason-{i}")
    append_trace(store, tenant_id=b, agent="bot", tool="b-secret-tool", kind="blocked",
                 reasoning="b-secret")

    # Tenant A: full page is exactly A's rows, newest first, with the defense-shape fields.
    rows, _ = store.list(tenant_id=a, limit=50)
    assert len(rows) == 5
    assert all(r["tenant_id"] == a for r in rows)
    assert [r["tool"] for r in rows] == [f"tool-{i}" for i in (4, 3, 2, 1, 0)]
    assert "b-secret-tool" not in {r["tool"] for r in rows}, "RLS leak: A read B's trace"

    # Tenant B sees only its own.
    rows_b, _ = store.list(tenant_id=b, limit=50)
    assert [r["tool"] for r in rows_b] == ["b-secret-tool"]

    # Keyset pagination: limit-2 pages cover all 5, no overlap, then a None cursor.
    seen, cursor = [], None
    for _ in range(4):
        page, cursor = store.list(tenant_id=a, limit=2, cursor=cursor)
        seen += [r["id"] for r in page]
        if cursor is None:
            break
    assert len(seen) == 5 and len(set(seen)) == 5


@pytest.mark.integration
def test_control_settings_rls_isolation(make_stores):
    make_settings, _ = make_stores
    store = make_settings()
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    store.set_killswitch(a, True)
    store.set_autonomy(a, "L3")

    row_a = store.get(a)
    assert row_a["killswitch_engaged"] is True and row_a["autonomy_level"] == "L3"
    # Tenant B's scope cannot see (or be affected by) A's row.
    assert store.get(b) is None


@pytest.mark.integration
def test_killswitch_multi_instance_flip_visible(make_stores):
    """Two separate store instances + facades == two API tasks sharing only Aurora."""
    make_settings, _ = make_stores
    sentinel_store = make_settings()
    task1 = PersistedKillSwitch(make_settings(), ttl_seconds=0.0)
    task2 = PersistedKillSwitch(make_settings(), ttl_seconds=0.0)
    a, b = str(uuid.uuid4()), str(uuid.uuid4())

    assert task2.is_paused(a) is False
    task1.set(a, True)                       # task 1 flips tenant A
    assert task2.is_paused(a) is True        # task 2 sees it through Pg
    assert task2.is_paused(b) is False       # tenant B untouched
    assert task2.status(a) == {"engaged": True, "scope": "tenant"}

    task1.set(a, False)
    assert task2.is_paused(a) is False

    # Global scope: one flip pauses EVERY tenant on EVERY instance; release restores.
    task1.set("operator", True, scope="global")
    try:
        assert task2.is_paused(a) is True and task2.is_paused(b) is True
        assert task2.status(b) == {"engaged": True, "scope": "global"}
    finally:
        task1.set("operator", False, scope="global")  # never leave a shared DB globally paused
    assert task2.is_paused(a) is False
    # The sentinel row exists and is disengaged (visible only under its own scope).
    sentinel = sentinel_store.get(GLOBAL_CONTROL_TENANT)
    assert sentinel is not None and sentinel["killswitch_engaged"] is False


@pytest.mark.integration
def test_autonomy_dial_multi_instance(make_stores):
    make_settings, _ = make_stores
    dial1 = PersistedAutonomyDial(make_settings(), ttl_seconds=0.0)
    dial2 = PersistedAutonomyDial(make_settings(), ttl_seconds=0.0)
    t = str(uuid.uuid4())
    assert dial2.get(t) is Level.L1          # unseeded -> the default
    dial1.set(t, Level.L2)
    assert dial2.get(t) is Level.L2          # instance 2 reads instance 1's dial through Pg
    assert dial2.provider(t) is Level.L2
