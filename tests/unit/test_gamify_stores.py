"""Unit: the Sell (gamification) in-memory stores isolate by explicit tenant filtering.

No DB, no RLS — isolation here is pure tenant filtering inside the store (the default offline path
+ the test doubles). Every op is scoped by its tenant_id argument, so a caller can never read or
aggregate another tenant's members or points. Fast (microseconds); the cross-thread Pg/RLS proof
lives in tests/integration.
"""
import pytest

from api.gamify_stores import InMemoryMemberStore, InMemoryPointsStore


@pytest.mark.unit
def test_inmemory_member_store_scopes_by_tenant():
    store = InMemoryMemberStore()
    store.upsert("A", "u1", display_name="Alice", role="rep")
    store.upsert("B", "u1", display_name="Bob")  # same user_id, different tenant

    # Each tenant sees only its own roster — never the foreign row.
    a_rows = store.list("A")
    b_rows = store.list("B")
    assert {r["tenant_id"] for r in a_rows} == {"A"}
    assert {r["tenant_id"] for r in b_rows} == {"B"}
    assert a_rows[0]["display_name"] == "Alice"
    assert b_rows[0]["display_name"] == "Bob"
    assert store.list("C") == []


@pytest.mark.unit
def test_inmemory_member_upsert_refreshes_without_erasing():
    store = InMemoryMemberStore()
    store.upsert("A", "u1", display_name="Alice", role="rep")

    # A bare presence-ping (no name) must not erase the known display_name/role.
    store.upsert("A", "u1")
    row = store.list("A")[0]
    assert row["display_name"] == "Alice"
    assert row["role"] == "rep"

    # An explicit new name wins.
    store.upsert("A", "u1", display_name="Alice B.")
    assert store.list("A")[0]["display_name"] == "Alice B."
    assert len(store.list("A")) == 1  # still one row (upsert, not insert)


@pytest.mark.unit
def test_inmemory_points_store_scopes_by_tenant():
    store = InMemoryPointsStore()
    store.append({"tenant_id": "A", "user_id": "u1", "event_type": "deal_won", "points": 10})
    store.append({"tenant_id": "A", "user_id": "u1", "event_type": "call", "points": 5})
    store.append({"tenant_id": "B", "user_id": "u1", "event_type": "deal_won", "points": 99})

    # Tenant A's leaderboard sums only A's events — B's 99 never leaks in.
    a_board = store.leaderboard("A")
    assert a_board == [{"user_id": "u1", "display_name": None, "points": 15, "events": 2}]

    b_board = store.leaderboard("B")
    assert b_board[0]["points"] == 99

    # Per-user summary is tenant-scoped too.
    assert store.user_summary("A", "u1") == {"user_id": "u1", "points": 15, "events": 2}
    assert store.user_summary("B", "u1")["points"] == 99
    # A user unknown in this tenant aggregates to zero, never to the other tenant's total.
    assert store.user_summary("A", "ghost") == {"user_id": "ghost", "points": 0, "events": 0}
    assert store.leaderboard("C") == []


@pytest.mark.unit
def test_inmemory_points_leaderboard_orders_and_filters_since():
    store = InMemoryPointsStore()
    store.append({"tenant_id": "A", "user_id": "low", "points": 3, "occurred_at": "2026-01-01"})
    store.append({"tenant_id": "A", "user_id": "high", "points": 8, "occurred_at": "2026-06-01"})

    # Highest total first.
    board = store.leaderboard("A")
    assert [r["user_id"] for r in board] == ["high", "low"]

    # `since` filters out earlier events (inclusive lower bound).
    recent = store.leaderboard("A", since="2026-03-01")
    assert recent == [{"user_id": "high", "display_name": None, "points": 8, "events": 1}]


@pytest.mark.unit
def test_inmemory_points_leaderboard_joins_member_display_name():
    members = InMemoryMemberStore()
    members.upsert("A", "u1", display_name="Alice")
    points = InMemoryPointsStore(members=members)
    points.append({"tenant_id": "A", "user_id": "u1", "points": 7})

    board = points.leaderboard("A")
    assert board[0]["display_name"] == "Alice"


@pytest.mark.unit
def test_inmemory_points_append_requires_tenant():
    store = InMemoryPointsStore()
    with pytest.raises(ValueError):
        store.append({"user_id": "u1", "points": 1})
