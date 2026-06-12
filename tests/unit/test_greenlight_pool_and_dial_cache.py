"""Unit: PgApprovalStore pool-exhaustion retry + control-dial cache read-your-own-write.

Coverage for two audit-flagged gaps: the _getconn retry loop had no test proving it survives a
momentary pool exhaustion (or that a non-exhaustion PoolError still raises), and the persisted
dial's TTL cache had no same-instance read-after-write proof.
"""
import pytest

from api.control.greenlight import PgApprovalStore
from api.control.settings import (
    InMemoryControlSettings,
    PersistedAutonomyDial,
    PersistedKillSwitch,
)
from api.control.types import Level

psycopg2 = pytest.importorskip("psycopg2")
pytest.importorskip("psycopg2.pool")  # not auto-imported by the parent package


def _bare_store(pool) -> PgApprovalStore:
    """A PgApprovalStore with no DSN/connection — just the pieces _getconn touches."""
    store = object.__new__(PgApprovalStore)
    store._psycopg2 = psycopg2
    store._pool = pool
    return store


@pytest.mark.unit
def test_getconn_retries_through_momentary_pool_exhaustion():
    class ExhaustiblePool:
        def __init__(self):
            self.attempts = 0

        def getconn(self):
            self.attempts += 1
            if self.attempts < 3:
                raise psycopg2.pool.PoolError("connection pool exhausted")
            return "the-conn"

    pool = ExhaustiblePool()
    assert _bare_store(pool)._getconn() == "the-conn"
    assert pool.attempts == 3


@pytest.mark.unit
def test_getconn_raises_immediately_for_non_exhaustion_pool_errors():
    class ClosedPool:
        def getconn(self):
            raise psycopg2.pool.PoolError("pool is closed")

    with pytest.raises(psycopg2.pool.PoolError, match="closed"):
        _bare_store(ClosedPool())._getconn()


@pytest.mark.unit
def test_autonomy_dial_read_your_own_write_despite_a_long_ttl():
    # A long TTL would serve the stale cached level forever if set() failed to invalidate.
    dial = PersistedAutonomyDial(InMemoryControlSettings(), ttl_seconds=3600)
    assert dial.get("t1") is Level.L1  # default, now cached
    dial.set("t1", Level.L3)
    assert dial.get("t1") is Level.L3


@pytest.mark.unit
def test_killswitch_read_your_own_write_despite_a_long_ttl():
    ks = PersistedKillSwitch(InMemoryControlSettings(), ttl_seconds=3600)
    assert ks.is_paused("t1") is False  # cached
    ks.set("t1", True)
    assert ks.is_paused("t1") is True
    ks.set("t1", False)
    assert ks.is_paused("t1") is False
