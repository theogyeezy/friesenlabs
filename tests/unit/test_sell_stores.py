"""Unit: the Sell points-store reads the /sell routes ride — tenant + user scoped.

Two ADDITIVE reads on the points ledger (api/gamify_stores.py), proven here to isolate by explicit
tenant filtering (the offline path; the cross-thread Pg/RLS proof lives in tests/integration):
  * user_events — one rep's scored events ({event_type, points, occurred_at}), newest first, for the
    /sell/me streak + today-progress derivation.
  * leaderboard_rows — the per-user leaderboard rows STAMPED with tenant_id, so the route can do a
    defense-in-depth tenant re-check (mirror /views) before stripping the internal id from the wire.
"""
import pytest

from api.gamify_stores import InMemoryMemberStore, InMemoryPointsStore
from shared.gamify_rules import DEAL_CLOSED_WON


@pytest.mark.unit
def test_user_events_scopes_by_tenant_and_user_newest_first():
    store = InMemoryPointsStore()
    store.append({"tenant_id": "A", "user_id": "u1", "event_type": DEAL_CLOSED_WON,
                  "points": 10, "occurred_at": "2026-06-10T00:00:00+00:00"})
    store.append({"tenant_id": "A", "user_id": "u1", "event_type": DEAL_CLOSED_WON,
                  "points": 10, "occurred_at": "2026-06-12T00:00:00+00:00"})
    store.append({"tenant_id": "A", "user_id": "u2", "event_type": DEAL_CLOSED_WON,
                  "points": 10, "occurred_at": "2026-06-12T00:00:00+00:00"})
    store.append({"tenant_id": "B", "user_id": "u1", "event_type": DEAL_CLOSED_WON,
                  "points": 99, "occurred_at": "2026-06-12T00:00:00+00:00"})

    events = store.user_events("A", "u1")
    # Only A/u1's two events — never u2's, never B's.
    assert [e["occurred_at"] for e in events] == \
        ["2026-06-12T00:00:00+00:00", "2026-06-10T00:00:00+00:00"]  # newest first
    assert all(e["event_type"] == DEAL_CLOSED_WON and e["points"] == 10 for e in events)
    assert store.user_events("A", "ghost") == []
    assert store.user_events("B", "u1")[0]["points"] == 99


@pytest.mark.unit
def test_user_events_filters_since_inclusive():
    store = InMemoryPointsStore()
    store.append({"tenant_id": "A", "user_id": "u1", "points": 10,
                  "occurred_at": "2026-01-01T00:00:00+00:00"})
    store.append({"tenant_id": "A", "user_id": "u1", "points": 10,
                  "occurred_at": "2026-06-01T00:00:00+00:00"})
    recent = store.user_events("A", "u1", since="2026-03-01T00:00:00+00:00")
    assert [e["occurred_at"] for e in recent] == ["2026-06-01T00:00:00+00:00"]


@pytest.mark.unit
def test_leaderboard_rows_carry_tenant_and_scope_by_tenant():
    members = InMemoryMemberStore()
    members.upsert("A", "u1", display_name="Alice")
    store = InMemoryPointsStore(members=members)
    store.append({"tenant_id": "A", "user_id": "u1", "points": 10})
    store.append({"tenant_id": "A", "user_id": "u2", "points": 4})
    store.append({"tenant_id": "B", "user_id": "u1", "points": 99})

    rows = store.leaderboard_rows("A")
    # Every row is stamped with the requested tenant (the field the route re-checks).
    assert {r["tenant_id"] for r in rows} == {"A"}
    # Highest first, display_name joined, B's 99 never leaks in.
    assert [r["user_id"] for r in rows] == ["u1", "u2"]
    assert rows[0]["display_name"] == "Alice"
    assert rows[0]["points"] == 10
    assert store.leaderboard_rows("C") == []
