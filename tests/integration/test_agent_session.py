"""Integration: a coordinator session delegates to specialists and a tool runs tenant-scoped.

Uses FakeRuntime — no real Anthropic. Exercises the Phase 4 'done when' shape offline.
"""
import pytest

from agents import coordinator
from agents.runtime import get_runtime
from agents.tools.base import ToolContext
from agents.tools.readonly import ReadCrm


class FakeDB:
    def __init__(self):
        self.tenant = None

    def set_tenant(self, t):
        self.tenant = t

    def read(self, entity, limit):
        # Return rows "belonging" to whatever tenant is currently bound.
        return [{"entity": entity, "tenant": self.tenant}]


@pytest.mark.integration
def test_session_delegates_and_tool_is_tenant_scoped():
    rt = get_runtime({"runtime": "fake"})
    coord_id = coordinator.build(rt)
    session = rt.create_session(coord_id, tenant_id="tenant-A", vault_id=rt.create_vault("A", "tenant-A"))

    resp = rt.send_message(session, "Research and follow up on my hottest lead")
    # The coordinator delegated to every specialist.
    assert set(resp["delegations"]) == {"scout", "nadia", "margo", "ledger", "echo", "pip", "critic"}
    assert resp["tenant_id"] == "tenant-A"

    # A read-only tool executes against the right tenant's data (RLS bound from session tenant).
    db = FakeDB()
    ctx = ToolContext(tenant_id=session.tenant_id, db=db)
    out = ReadCrm().invoke(ctx, entity="deals")
    assert db.tenant == "tenant-A"
    assert out["result"]["rows"][0]["tenant"] == "tenant-A"
