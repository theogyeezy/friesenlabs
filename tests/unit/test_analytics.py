"""Unit: conversation analytics (Build Guide Step 38).

Events persist tenant-scoped; the four event types round-trip through the store.
"""
import pytest

from conv.analytics import Analytics, Event, EventType, InMemoryAnalyticsStore


@pytest.mark.unit
def test_records_event_and_stamps_timestamp():
    a = Analytics(clock=lambda: 123.0)
    rec = a.record(Event(tenant_id="tenant-A", type=EventType.UTTERANCE, payload={"text": "hi"}))
    assert rec["tenant_id"] == "tenant-A"
    assert rec["type"] == "utterance"
    assert rec["ts"] == 123.0
    assert rec["payload"] == {"text": "hi"}


@pytest.mark.unit
def test_explicit_timestamp_is_preserved():
    a = Analytics(clock=lambda: 999.0)
    rec = a.record(Event(tenant_id="t", type=EventType.CLICK, ts=5.0))
    assert rec["ts"] == 5.0


@pytest.mark.unit
def test_all_event_types_round_trip():
    a = Analytics()
    for et in (EventType.UTTERANCE, EventType.TOOL_CALL, EventType.APPROVAL, EventType.CLICK):
        a.record(Event(tenant_id="tenant-A", type=et))
    rows = a.list("tenant-A")
    assert {r["type"] for r in rows} == {"utterance", "tool_call", "approval", "click"}


@pytest.mark.unit
def test_list_filters_by_type():
    a = Analytics()
    a.record(Event(tenant_id="t", type=EventType.UTTERANCE))
    a.record(Event(tenant_id="t", type=EventType.TOOL_CALL))
    a.record(Event(tenant_id="t", type=EventType.TOOL_CALL))
    assert len(a.list("t", type=EventType.TOOL_CALL)) == 2
    assert len(a.list("t", type="utterance")) == 1


@pytest.mark.unit
def test_events_are_tenant_scoped():
    store = InMemoryAnalyticsStore()
    a = Analytics(store)
    a.record(Event(tenant_id="tenant-A", type=EventType.UTTERANCE, payload={"text": "A"}))
    a.record(Event(tenant_id="tenant-B", type=EventType.UTTERANCE, payload={"text": "B"}))

    a_rows = a.list("tenant-A")
    assert len(a_rows) == 1
    assert a_rows[0]["payload"]["text"] == "A"
    # Tenant-B's event never appears for tenant-A.
    assert all(r["tenant_id"] == "tenant-A" for r in a_rows)
    assert a.list("tenant-C") == []
