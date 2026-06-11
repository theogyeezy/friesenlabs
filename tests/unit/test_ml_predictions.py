"""Unit: the prediction log + live AUC (ml/predictions.py) — drift's real inputs.

InMemory + mocked-Pg implementations of one protocol (log / record_outcome / scored_outcomes),
and live_auc's honesty rules (insufficient evidence / single-class -> None, never a made-up
number). The Pg path proves the per-op SET LOCAL pattern and that outcome resolution only
touches still-open rows.
"""
import pytest

from ml.predictions import (
    MIN_LIVE_SAMPLES,
    InMemoryPredictionLog,
    PgPredictionLog,
    live_auc,
)

# --------------------------------------------------------------------------- in-memory protocol


@pytest.mark.unit
def test_log_then_resolve_then_scored_outcomes():
    log = InMemoryPredictionLog()
    log.log("t1", deal_id="d1", model_version=1, score=0.9, features={"amount": 1})
    log.log("t1", deal_id="d2", model_version=1, score=0.2)
    assert log.scored_outcomes("t1") == []          # nothing resolved yet

    assert log.record_outcome("t1", "d1", 1) == 1
    assert log.record_outcome("t1", "d1", 0) == 0   # already resolved — never overwritten
    assert log.scored_outcomes("t1") == [(0.9, 1)]


@pytest.mark.unit
def test_in_memory_log_is_tenant_scoped():
    log = InMemoryPredictionLog()
    log.log("t1", deal_id="d1", model_version=1, score=0.9)
    log.log("t2", deal_id="d1", model_version=1, score=0.1)
    assert log.record_outcome("t2", "d1", 0) == 1
    assert log.scored_outcomes("t1") == []          # t1's prediction untouched
    assert log.scored_outcomes("t2") == [(0.1, 0)]


# --------------------------------------------------------------------------- live AUC honesty

def _resolved(log, tenant, n, *, good=True):
    for i in range(n):
        outcome = i % 2
        score = (0.8 if outcome else 0.2) if good else (0.2 if outcome else 0.8)
        deal = f"d{i}"
        log.log(tenant, deal_id=deal, model_version=1, score=score)
        log.record_outcome(tenant, deal, outcome)


@pytest.mark.unit
def test_live_auc_requires_enough_resolved_outcomes():
    log = InMemoryPredictionLog()
    _resolved(log, "t1", MIN_LIVE_SAMPLES - 1)
    out = live_auc(log, "t1")
    assert out["auc"] is None and "resolved outcomes" in out["reason"]


@pytest.mark.unit
def test_live_auc_requires_both_outcome_classes():
    log = InMemoryPredictionLog()
    for i in range(MIN_LIVE_SAMPLES):
        log.log("t1", deal_id=f"d{i}", model_version=1, score=0.5)
        log.record_outcome("t1", f"d{i}", 1)        # all won — AUC undefined
    out = live_auc(log, "t1")
    assert out["auc"] is None and "single-class" in out["reason"]


@pytest.mark.unit
def test_live_auc_computes_real_separation():
    good, bad = InMemoryPredictionLog(), InMemoryPredictionLog()
    _resolved(good, "t1", 40, good=True)
    _resolved(bad, "t1", 40, good=False)
    assert live_auc(good, "t1")["auc"] == 1.0       # perfectly ranked
    assert live_auc(bad, "t1")["auc"] == 0.0        # perfectly anti-ranked
    assert live_auc(good, "t1")["n"] == 40


# --------------------------------------------------------------------------- Pg path (mocked)

class FakeCursor:
    def __init__(self, log, rows):
        self.log = log
        self._rows = rows
        self.rowcount = 1
        self.description = [("score",), ("outcome",)]

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, log, rows):
        self.log = log
        self._rows = rows
        self.commits = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self.log, self._rows)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def _pg(rows=()):
    log: list = []
    store = PgPredictionLog(conn_factory=lambda: FakeConn(log, list(rows)))
    return store, log


@pytest.mark.unit
def test_pg_log_set_local_precedes_insert_with_tenant_in_values():
    store, log = _pg()
    store.log("tenant-A", deal_id="d1", model_version=3, score=0.7, features={"amount": 9})
    assert log[0] == ("SET LOCAL app.current_tenant = %s", ("tenant-A",))
    sql, params = log[1]
    assert sql.startswith("INSERT INTO predictions")
    assert params[0] == "tenant-A" and params[1] == "d1" and params[2] == 3
    assert not any(s.startswith("SET app.current_tenant") for s, _ in log)


@pytest.mark.unit
def test_pg_record_outcome_updates_only_open_rows():
    store, log = _pg()
    updated = store.record_outcome("tenant-A", "d1", 1)
    assert updated == 1
    sql, params = log[1]
    assert "SET outcome = %s" in sql and "outcome IS NULL" in sql
    assert params == (1, "d1")
    # No hand-written tenant filter — RLS (via the SET LOCAL GUC) scopes the UPDATE.
    assert "tenant_id =" not in sql


@pytest.mark.unit
def test_pg_scored_outcomes_returns_chronological_pairs():
    store, log = _pg(rows=[(0.9, 1), (0.2, 0)])     # query returns newest first
    pairs = store.scored_outcomes("tenant-A", limit=50)
    assert pairs == [(0.2, 0), (0.9, 1)]            # reversed -> oldest first
    sql, params = log[1]
    assert "outcome IS NOT NULL" in sql and params == (50,)
