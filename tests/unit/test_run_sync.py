"""Unit: the runnable ingestion entrypoint (python -m ingest.run_sync).

All offline. Proves:
  * import safety + the strict INGEST_REAL_STORES switch semantics
  * deploy invariance: DB_*/UPLIFT_DB_URL alone never select real stores —
    only the NEW deliberate INGEST_REAL_STORES flag does
  * --tenant / --all tenant resolution (INGEST_TENANTS) and exit codes:
    0 = synced (or nothing to do), 1 = any tenant failed, 2 = usage error
  * real mode with the switch on but no DSN fails LOUDLY (exit 1), and with a
    DSN builds the pooled Pg stores
"""
import pytest

import psycopg2
import psycopg2.pool

import ingest.run_sync as run_sync
from ingest import EMBEDDING_DIM
from ingest.pipeline import (
    InMemoryCursorStore,
    InMemoryDocumentStore,
    PgCursorStore,
    PgDocumentStore,
)
from shared.config import (
    ENV_INGEST_RAW_BUCKET,
    ENV_INGEST_REAL_STORES,
    ENV_INGEST_TENANTS,
)

ENV_NAMES = (
    ENV_INGEST_REAL_STORES, ENV_INGEST_TENANTS, ENV_INGEST_RAW_BUCKET,
    "UPLIFT_DB_URL", "DB_USER", "DB_PASS", "DB_HOST", "DB_NAME", "DB_PORT",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts with no ingest/DB env (offline posture)."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------- switch
@pytest.mark.unit
def test_real_mode_off_by_default_and_strict(monkeypatch):
    assert run_sync.real_mode() is False
    for junk in ("True", "yes", "on", " 1", "1 ", "TRUE"):
        monkeypatch.setenv(ENV_INGEST_REAL_STORES, junk)
        assert run_sync.real_mode() is False, junk
    for ok in ("true", "1"):
        monkeypatch.setenv(ENV_INGEST_REAL_STORES, ok)
        assert run_sync.real_mode() is True


@pytest.mark.unit
def test_deploy_invariance_db_env_alone_never_selects_real_stores(monkeypatch):
    # The live API task already injects DB_* — without the NEW deliberate flag
    # those must NOT flip run_sync to real adapters.
    monkeypatch.setenv("UPLIFT_DB_URL", "postgresql://crm_app:x@h/db")
    monkeypatch.setenv("DB_USER", "crm_app")
    monkeypatch.setenv("DB_PASS", "x")
    monkeypatch.setenv("DB_HOST", "h")
    store, cursors = run_sync.build_stores()
    assert isinstance(store, InMemoryDocumentStore)
    assert isinstance(cursors, InMemoryCursorStore)
    assert run_sync.build_embedder() is run_sync._stub_embedder


@pytest.mark.unit
def test_real_mode_without_dsn_fails_loudly(monkeypatch):
    monkeypatch.setenv(ENV_INGEST_REAL_STORES, "1")
    with pytest.raises(RuntimeError, match="no DSN"):
        run_sync.build_stores()
    # ...and main() surfaces it as exit code 1, not a crash.
    assert run_sync.main(["--tenant", "t1"]) == 1


@pytest.mark.unit
def test_real_mode_with_dsn_builds_pooled_pg_stores(monkeypatch):
    class FakePool:
        def __init__(self):
            self.log = []

        def getconn(self):
            raise AssertionError("no op should run in this test")

        def putconn(self, conn):
            pass

    monkeypatch.setenv(ENV_INGEST_REAL_STORES, "true")
    monkeypatch.setenv("UPLIFT_DB_URL", "postgresql://crm_app:x@h/db")
    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool",
                        lambda minc, maxc, dsn: FakePool())
    store, cursors = run_sync.build_stores()
    assert isinstance(store, PgDocumentStore)
    assert isinstance(cursors, PgCursorStore)


# --------------------------------------------------------------------------- tenants + exit codes
@pytest.mark.unit
def test_usage_error_without_tenant_or_all_exits_2():
    with pytest.raises(SystemExit) as exc:
        run_sync.main([])
    assert exc.value.code == 2


@pytest.mark.unit
def test_offline_single_tenant_sync_exits_0():
    assert run_sync.main(["--tenant", "t1"]) == 0


@pytest.mark.unit
def test_all_with_empty_ingest_tenants_is_a_clean_noop():
    assert run_sync.main(["--all"]) == 0


@pytest.mark.unit
def test_all_resolves_tenants_from_env_deduped(monkeypatch):
    monkeypatch.setenv(ENV_INGEST_TENANTS, " t1, t2 ,t1,, t3 ")
    synced = []
    monkeypatch.setattr(run_sync, "sync_tenant",
                        lambda tid, *a, **k: synced.append(tid) or run_sync.SyncResult())
    assert run_sync.main(["--all"]) == 0
    assert synced == ["t1", "t2", "t3"]


@pytest.mark.unit
def test_repeated_tenant_flags_deduped(monkeypatch):
    synced = []
    monkeypatch.setattr(run_sync, "sync_tenant",
                        lambda tid, *a, **k: synced.append(tid) or run_sync.SyncResult())
    assert run_sync.main(["--tenant", "a", "--tenant", "b", "--tenant", "a"]) == 0
    assert synced == ["a", "b"]


@pytest.mark.unit
def test_one_failing_tenant_exits_1_but_others_still_sync(monkeypatch):
    synced = []

    def fake_sync(tenant_id, *a, **k):
        if tenant_id == "bad":
            raise RuntimeError("connector exploded")
        synced.append(tenant_id)
        return run_sync.SyncResult(pulled=1)

    monkeypatch.setattr(run_sync, "sync_tenant", fake_sync)
    rc = run_sync.main(["--tenant", "good1", "--tenant", "bad", "--tenant", "good2"])
    assert rc == 1
    assert synced == ["good1", "good2"]  # the bad tenant didn't stop the rest


# --------------------------------------------------------------------------- offline pieces
@pytest.mark.unit
def test_stub_embedder_dimensionality():
    vec = run_sync._stub_embedder("hello")
    assert len(vec) == EMBEDDING_DIM
    assert all(isinstance(x, float) for x in vec)
    assert vec == run_sync._stub_embedder("hello")  # deterministic


@pytest.mark.unit
def test_offline_connector_runs_full_sync_path():
    store, cursors = run_sync.build_stores()
    res = run_sync.run_one(
        "t1", store=store, cursors=cursors,
        embedder=run_sync.build_embedder(), raw_sink=run_sync.build_raw_sink(),
    )
    assert res.pulled == 0      # stub source pulls nothing...
    assert res.embedded == 0    # ...but auth -> pull -> land -> cursor all ran


@pytest.mark.unit
def test_offline_raw_sink_is_in_memory():
    from ingest.pipeline import InMemoryRawSink
    assert isinstance(run_sync.build_raw_sink(), InMemoryRawSink)
