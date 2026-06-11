"""Unit: PgControlSettingsStore + PgTraceStore bind the tenant with SET LOCAL before every op.

Fake psycopg2 plumbing (no DB) — proves the security-critical guarantees the integration tests
re-prove live: the per-op `SET LOCAL app.current_tenant` transaction (the PgApprovalStore
pattern), the upsert shapes, the keyset pagination SQL, and that a malformed cursor is rejected
BEFORE any SQL is issued.
"""
import json

import pytest

from api.control.traces import InMemoryTraceStore, PgTraceStore, append_trace
from api.pg_clients import PgControlSettingsStore


# --------------------------------------------------------------------------- fakes
class FakeCursor:
    def __init__(self, log, rows, description):
        self.log = log
        self._rows = rows
        self.description = description

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, log, rows=None, description=None):
        self.log = log
        self._rows = rows or []
        self._description = description
        self.closed = False

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.log, self._rows, self._description)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def _store(cls, rows=None, description=None):
    log: list = []
    store = cls(conn_factory=lambda: FakeConn(log, rows, description))
    return store, log


def _sql(log):
    return [s for s, _ in log]


# --------------------------------------------------------------------------- control settings
@pytest.mark.unit
def test_control_settings_every_op_binds_tenant_first():
    store, log = _store(PgControlSettingsStore,
                        rows=[{"tenant_id": "A", "autonomy_level": "L2",
                               "killswitch_engaged": True}])
    store.get("A")
    store.set_killswitch("A", True)
    store.set_autonomy("A", "L2")
    sql = _sql(log)
    # Each of the 3 ops opens with the SET LOCAL bind (per-op txn; never session-level SET).
    assert [s for s in sql if s.startswith("SET LOCAL app.current_tenant")] == \
        ["SET LOCAL app.current_tenant = %s"] * 3
    assert not any(s.startswith("SET app.current_tenant") for s in sql)
    binds = [p for s, p in log if s.startswith("SET LOCAL")]
    assert binds == [("A",)] * 3


@pytest.mark.unit
def test_control_settings_upsert_shapes():
    store, log = _store(PgControlSettingsStore)
    store.set_killswitch("A", True)
    store.set_autonomy("A", "L3")
    sql = _sql(log)
    ks = next(s for s in sql if "killswitch_engaged" in s and s.startswith("INSERT"))
    assert "ON CONFLICT (tenant_id) DO UPDATE" in ks         # a flip must WIN over the seed
    assert "killswitch_updated_at = now()" in ks
    al = next(s for s in sql if "autonomy_level" in s and s.startswith("INSERT"))
    assert "ON CONFLICT (tenant_id) DO UPDATE SET autonomy_level = EXCLUDED.autonomy_level" in al
    # Values ride as bind params.
    assert ("A", True) in [p for _, p in log]
    assert ("A", "L3") in [p for _, p in log]


@pytest.mark.unit
def test_control_settings_get_normalizes_row_and_misses():
    store, _ = _store(PgControlSettingsStore,
                      rows=[("A", "L2", True)],
                      description=[("tenant_id",), ("autonomy_level",), ("killswitch_engaged",)])
    row = store.get("A")
    assert row == {"tenant_id": "A", "autonomy_level": "L2", "killswitch_engaged": True}
    empty, _ = _store(PgControlSettingsStore)
    assert empty.get("A") is None


@pytest.mark.unit
def test_set_autonomy_rejects_junk_before_any_sql():
    store, log = _store(PgControlSettingsStore)
    with pytest.raises(ValueError):
        store.set_autonomy("A", "L9; DROP TABLE tenant_settings")
    assert log == []  # nothing was issued


# --------------------------------------------------------------------------- trace store
@pytest.mark.unit
def test_trace_append_binds_tenant_and_packs_payload():
    store, log = _store(PgTraceStore, rows=[("uuid-1",)], description=[("id",)])
    tid = append_trace(store, tenant_id="A", agent="nadia", tool="send_email",
                       kind="pending_approval", inputs={"to": "x"}, reasoning="why")
    assert tid == "uuid-1"
    sql = _sql(log)
    assert sql[0] == "SET LOCAL app.current_tenant = %s"
    ins = next(s for s in sql if s.startswith("INSERT INTO traces"))
    assert "(tenant_id, agent, kind, payload)" in ins and "%s::jsonb" in ins
    params = [p for s, p in log if s.startswith("INSERT")][0]
    payload = json.loads(params[3])
    assert payload["tool"] == "send_email" and payload["reasoning"] == "why"
    assert params[:3] == ("A", "nadia", "pending_approval")


@pytest.mark.unit
def test_trace_list_keyset_pagination_sql_and_cursor():
    ts = "2026-06-10T12:00:00+00:00"
    rid = "0c6f3a52-4f7e-4b34-9a55-2f4f53b8b001"
    rows = [(rid, "A", ts, "nadia", "executed", {"tool": "read_crm", "reasoning": "r",
                                                 "inputs": None, "outputs": None, "tokens": None})]
    desc = [("id",), ("tenant_id",), ("created_at",), ("agent",), ("kind",), ("payload",)]
    store, log = _store(PgTraceStore, rows=rows, description=desc)

    page, cursor = store.list(tenant_id="A", limit=1)
    assert page[0]["id"] == rid and page[0]["tool"] == "read_crm"
    assert page[0]["ts"] == ts and page[0]["kind"] == "executed"
    assert cursor == f"{ts}|{rid}"        # full page -> a next-page cursor

    page2, _ = store.list(tenant_id="A", limit=1, cursor=cursor)
    sql = _sql(log)
    keyset = next(s for s in sql if "WHERE (created_at, id) <" in s)
    assert "ORDER BY created_at DESC, id DESC LIMIT %s" in keyset
    params = [p for s, p in log if "WHERE (created_at, id) <" in s][0]
    assert params == (ts, rid, 1)

    # A short page means no further cursor.
    _, no_cursor = store.list(tenant_id="A", limit=5)
    assert no_cursor is None


@pytest.mark.unit
def test_trace_list_rejects_malformed_cursor_before_sql():
    store, log = _store(PgTraceStore)
    for bad in ("junk", "2026-06-10|not-a-uuid", "not-a-date|0c6f3a52-4f7e-4b34-9a55-2f4f53b8b001"):
        with pytest.raises(ValueError):
            store.list(tenant_id="A", cursor=bad)
    assert log == []


@pytest.mark.unit
def test_inmemory_trace_list_pagination_and_scoping():
    store = InMemoryTraceStore()
    for i in range(5):
        append_trace(store, tenant_id="A", agent=None, tool=f"t{i}", kind="executed",
                     reasoning=f"r{i}")
    append_trace(store, tenant_id="B", agent=None, tool="bee", kind="blocked")

    page1, c1 = store.list(tenant_id="A", limit=2)
    assert [r["tool"] for r in page1] == ["t4", "t3"]   # newest first
    page2, c2 = store.list(tenant_id="A", limit=2, cursor=c1)
    assert [r["tool"] for r in page2] == ["t2", "t1"]
    page3, c3 = store.list(tenant_id="A", limit=2, cursor=c2)
    assert [r["tool"] for r in page3] == ["t0"] and c3 is None
    # Tenant scoping: B's row never appears in A's pages.
    seen = {r["tool"] for r in page1 + page2 + page3}
    assert "bee" not in seen
    with pytest.raises(ValueError):
        store.list(tenant_id="A", cursor="zzz")
