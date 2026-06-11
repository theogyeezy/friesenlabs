"""Unit: the Cortex training-data loader (ml/data_loader.py).

The producer of the feature fields the serving code already expects: closed deals -> records in
the ml.features contract shape, tenant-scoped via the pooled per-op SET LOCAL pattern.
Mocked DB (no Postgres). Proves:
  * SET LOCAL app.current_tenant precedes the query, bound to THAT call's tenant
  * never a session-level SET (the historical leak pattern)
  * only closed stages are queried; won -> 1 / lost -> 0 labels
  * the records featurize cleanly under ml.features (the training<->serving contract)
  * determinism: same rows + same as_of -> the identical record list
"""
from datetime import datetime, timezone

import pytest

from ml import features
from ml.data_loader import (
    LOST_STAGES,
    WON_STAGES,
    PgTrainingDataLoader,
    StaticTrainingDataLoader,
    record_from_row,
)

AS_OF = datetime(2026, 6, 10, tzinfo=timezone.utc)

COLUMNS = ["deal_id", "amount", "stage", "created_at", "email", "phone", "n_activities"]
ROWS = [
    ("d-won", 12000.0, "closed_won", datetime(2026, 5, 31, tzinfo=timezone.utc),
     "a@x.com", None, 7),
    ("d-lost", 800.0, "closed_lost", datetime(2026, 4, 11, tzinfo=timezone.utc),
     None, "+15125550100", 2),
    ("d-bare", None, "won", None, None, None, None),  # defensive: nullable everything
]


class FakeCursor:
    def __init__(self, log, rows):
        self.log = log
        self._rows = rows
        self.description = [(c,) for c in COLUMNS]

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, log, rows):
        self.log = log
        self._rows = rows
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self.log, self._rows)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _loader(rows=ROWS):
    log: list = []
    conns: list = []

    def factory():
        conn = FakeConn(log, rows)
        conns.append(conn)
        return conn

    return PgTrainingDataLoader(conn_factory=factory), log, conns


@pytest.mark.unit
def test_set_local_precedes_the_query_with_that_calls_tenant():
    loader, log, conns = _loader()
    loader.load("tenant-A", as_of=AS_OF)
    assert log[0] == ("SET LOCAL app.current_tenant = %s", ("tenant-A",))
    assert "FROM deals" in log[1][0]
    # Never the session-level leak shape.
    assert not any(sql.startswith("SET app.current_tenant") for sql, _ in log)
    assert conns[0].commits == 1 and conns[0].closed  # per-op conn finished + released


@pytest.mark.unit
def test_queries_only_closed_stages_and_labels_won_lost():
    loader, log, _ = _loader()
    records = loader.load("tenant-A", as_of=AS_OF)
    (stages,) = log[1][1]
    assert set(stages) == set(WON_STAGES) | set(LOST_STAGES)
    by_id = {r["deal_id"]: r for r in records}
    assert by_id["d-won"]["booked"] == 1
    assert by_id["d-lost"]["booked"] == 0
    assert by_id["d-bare"]["booked"] == 1  # bare 'won' accepted defensively


@pytest.mark.unit
def test_records_match_the_serving_feature_contract():
    loader, _, _ = _loader()
    records = loader.load("tenant-A", as_of=AS_OF)
    X = features.featurize(records)          # must not raise; order is the contract
    y = features.labels(records)
    assert len(X) == len(y) == len(ROWS)
    assert all(len(row) == len(features.FEATURE_NAMES) for row in X)
    # Spot-check the engineered values against FEATURE_NAMES order.
    won = X[0]
    assert won[features.FEATURE_NAMES.index("amount")] == 12000.0
    assert won[features.FEATURE_NAMES.index("n_activities")] == 7.0
    assert won[features.FEATURE_NAMES.index("days_since_created")] == 10.0
    assert won[features.FEATURE_NAMES.index("has_email")] == 1.0
    assert won[features.FEATURE_NAMES.index("has_phone")] == 0.0


@pytest.mark.unit
def test_loader_is_deterministic_for_same_rows_and_as_of():
    loader1, _, _ = _loader()
    loader2, _, _ = _loader()
    a = loader1.load("tenant-A", as_of=AS_OF)
    b = loader1.load("tenant-A", as_of=AS_OF)   # same loader, second call
    c = loader2.load("tenant-A", as_of=AS_OF)   # fresh loader, same inputs
    assert a == b == c


@pytest.mark.unit
def test_record_from_row_normalizes_edge_shapes():
    # ISO-string and naive datetimes both work; missing amount/counters default to 0.
    rec = record_from_row({"deal_id": 7, "stage": "CLOSED_WON",
                           "created_at": "2026-06-01T00:00:00+00:00"}, AS_OF)
    assert rec["deal_id"] == "7"
    assert rec["booked"] == 1                       # stage matching is case-insensitive
    assert rec["days_since_created"] == 9.0
    assert rec["amount"] == 0.0 and rec["n_activities"] == 0
    naive = record_from_row({"deal_id": "x", "stage": "lost",
                             "created_at": datetime(2026, 6, 9)}, AS_OF)
    assert naive["booked"] == 0 and naive["days_since_created"] == 1.0
    # A created_at AFTER as_of clamps to 0, never negative.
    future = record_from_row({"deal_id": "y", "stage": "lost",
                              "created_at": datetime(2026, 7, 1, tzinfo=timezone.utc)}, AS_OF)
    assert future["days_since_created"] == 0.0


@pytest.mark.unit
def test_static_loader_round_trips_records():
    records = [{"deal_id": "d1", "amount": 1.0, "n_activities": 2, "days_since_created": 3,
                "email": None, "phone": None, "booked": 1}]
    out = StaticTrainingDataLoader(records).load("any-tenant")
    assert out == records
    out[0]["amount"] = 999  # defensive copy — the loader's source is never mutated
    assert StaticTrainingDataLoader(records).load("t")[0]["amount"] == 1.0


@pytest.mark.unit
def test_rollback_and_release_on_query_error():
    class BoomCursor(FakeCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if "FROM deals" in sql:
                raise RuntimeError("boom")

    log: list = []
    conn_holder: list = []

    def factory():
        conn = FakeConn(log, [])
        conn.cursor = lambda: BoomCursor(log, [])
        conn_holder.append(conn)
        return conn

    loader = PgTrainingDataLoader(conn_factory=factory)
    with pytest.raises(RuntimeError, match="boom"):
        loader.load("tenant-A", as_of=AS_OF)
    assert conn_holder[0].rollbacks == 1 and conn_holder[0].closed
