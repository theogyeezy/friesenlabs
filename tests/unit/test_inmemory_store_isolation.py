"""Unit: the in-memory stores isolate by explicit tenant filtering (the default offline path).

No DB, no RLS — isolation here is pure tenant filtering inside the store. After removing the stateful
`bind_tenant`, `get`/`update`/`list` must be tenant-scoped by their explicit tenant_id argument, so a
caller can never read or mutate another tenant's row. Fast (microseconds); the cross-thread Pg proof
lives in tests/integration/test_store_concurrency.py.
"""
import pytest

from api.control.greenlight import InMemoryApprovalStore
from api.views import InMemorySavedViewStore


@pytest.mark.unit
def test_inmemory_approval_store_scopes_by_tenant():
    store = InMemoryApprovalStore()
    aid = store.insert({"tenant_id": "A", "proposed_action": {"action": "send_email"},
                        "status": "pending", "reasoning": "r", "agent": "nadia", "value_at_stake": 1})

    # The owning tenant reads its row; another tenant gets None (never the foreign row).
    assert store.get("A", aid)["tenant_id"] == "A"
    assert store.get("B", aid) is None

    # list_pending is tenant-scoped.
    assert len(store.list_pending("A")) == 1
    assert store.list_pending("B") == []

    # A cross-tenant update is a silent no-op (the row is untouched for the real owner).
    store.update("B", aid, {"status": "approved"})
    assert store.get("A", aid)["status"] == "pending"

    # The owning tenant's update applies.
    store.update("A", aid, {"status": "approved"})
    assert store.get("A", aid)["status"] == "approved"
    assert store.list_pending("A") == []


@pytest.mark.unit
def test_inmemory_saved_view_store_scopes_by_tenant():
    store = InMemorySavedViewStore()
    store.insert({"tenant_id": "A", "view_id": "v1", "version": 1, "spec_json": {"k": 1},
                  "semantic_refs": [], "source_prompt": "p", "created_by": "u"})
    store.insert({"tenant_id": "B", "view_id": "v1", "version": 1, "spec_json": {"k": 2},
                  "semantic_refs": [], "source_prompt": "p", "created_by": "u"})

    # Same view_id, different tenants — each sees only its own.
    assert store.latest("A", "v1")["spec_json"] == {"k": 1}
    assert store.latest("B", "v1")["spec_json"] == {"k": 2}
    assert {r["tenant_id"] for r in store.list("A")} == {"A"}
    assert {r["tenant_id"] for r in store.list("B")} == {"B"}
    assert store.latest("C", "v1") is None
    assert store.list("C") == []
