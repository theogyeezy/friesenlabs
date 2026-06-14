"""Unit tests for PgCrmRecordsSink — the crm_records full-fidelity JSONB sink.

No DB: a FakeConn/FakeCursor records every (sql, params) so we assert the SET LOCAL tenant
scoping, the UPSERT shape (columns, ::jsonb casts, ON CONFLICT DO UPDATE), GUC-derived tenant_id
(never hand-written), and per-row SAVEPOINT isolation — exactly like test_ingest_crm_sink.py.
"""
import json

import pytest

from ingest.connectors.hubspot_full import Record
from ingest.sinks import PgCrmRecordsSink

pytestmark = pytest.mark.unit

TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return None


class FakeConn:
    def __init__(self, log):
        self.log = log
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def _sink():
    log: list = []
    conn = FakeConn(log)
    return PgCrmRecordsSink(conn_factory=lambda: conn), conn, log


def _sqls(log):
    return [s for s, _ in log]


def test_set_local_binds_tenant_before_any_write():
    sink, conn, log = _sink()
    sink.upsert_records(TENANT_A, [Record("contacts", "1", {"email": "a@x.com"}, {}, "t1")])
    sqls = _sqls(log)
    assert sqls[0].startswith("SET LOCAL app.current_tenant")   # tenant bound first
    assert log[0][1] == (TENANT_A,)
    assert any(s == "SAVEPOINT crm_rec" for s in sqls)
    assert conn.commits == 1


def test_upsert_shape_jsonb_guc_tenant_and_on_conflict():
    sink, _conn, log = _sink()
    sink.upsert_records(TENANT_A, [Record(
        "deals", "42", {"dealname": "Big", "_media_refs": ["contract"]},
        {"companies": ["100"]}, "2026-06-01T00:00:00Z")])
    insert = next(s for s in _sqls(log) if s.startswith("INSERT INTO crm_records"))
    # tenant_id from the GUC, never hand-written
    assert "current_setting('app.current_tenant')::uuid" in insert
    assert "%s::jsonb" in insert                                   # properties + associations cast
    assert "ON CONFLICT (tenant_id, source, object_type, source_ref_id) DO UPDATE" in insert
    assert "archived_at = NULL" in insert                          # re-sync un-archives
    # params carry JSON-serialized bags (media kept as ref in properties)
    params = next(p for s, p in log if s.startswith("INSERT INTO crm_records"))
    assert params[0] == "hubspot" and params[1] == "deals" and params[2] == "42"
    assert json.loads(params[3]) == {"dealname": "Big", "_media_refs": ["contract"]}  # properties
    assert json.loads(params[4]) == {"companies": ["100"]}                            # associations
    assert params[5] == "2026-06-01T00:00:00Z"                                        # updated_at


def test_accepts_record_dataclass_and_plain_dict():
    sink, _conn, log = _sink()
    n = sink.upsert_records(TENANT_A, [
        Record("contacts", "1", {"email": "a@x.com"}, {}, None),
        {"object_type": "companies", "source_ref_id": "9", "properties": {"name": "Acme"},
         "associations": {}, "updated_at": None},
    ])
    assert n == 2
    inserts = [s for s in _sqls(log) if s.startswith("INSERT INTO crm_records")]
    assert len(inserts) == 2


def test_empty_records_is_zero_and_no_txn():
    sink, conn, log = _sink()
    assert sink.upsert_records(TENANT_A, []) == 0
    assert log == [] and conn.commits == 0


def test_record_missing_keys_is_reported_not_crashed():
    sink, _conn, _log = _sink()
    n = sink.upsert_records(TENANT_A, [{"object_type": "contacts"}])  # no source_ref_id
    assert n == 0
    assert sink.last_report.errors and "missing" in sink.last_report.errors[0]["reason"]


def test_row_error_isolated_by_savepoint_rollback():
    log: list = []

    class FailingCursor(FakeCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if sql.startswith("INSERT INTO crm_records"):
                raise RuntimeError("boom")

    class FailConn(FakeConn):
        def cursor(self):
            return FailingCursor(self.log)

    conn = FailConn(log)
    sink = PgCrmRecordsSink(conn_factory=lambda: conn)
    n = sink.upsert_records(TENANT_A, [Record("contacts", "1", {}, {}, None)])
    assert n == 0
    assert any(s == "ROLLBACK TO SAVEPOINT crm_rec" for s in _sqls(log))  # isolated, not aborted
    assert sink.last_report.errors[0]["reason"].startswith("RuntimeError")
