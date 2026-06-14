"""Unit: the Greenlight gate. Side-effecting tools NEVER execute — they propose. (The product's trust.)"""
import pytest

from agents.tools.base import InMemoryGreenlight, Policy, ToolContext
from agents.tools.readonly import QueryCube, ReadCrm, SearchRag
from agents.tools.sideeffecting import (
    CreateActivity,
    CreateDeal,
    DraftEmail,
    IssueQuote,
    SendEmail,
    UpdateContact,
    UpdateDeal,
)


class SpyDB:
    def __init__(self):
        self.tenant = None
        self.sent_email = False
        self.mutated = False

    def set_tenant(self, t):
        self.tenant = t

    def read(self, entity, limit):
        return [{"entity": entity}]


@pytest.mark.unit
def test_readonly_tools_are_auto():
    for tool in (SearchRag(), QueryCube(), ReadCrm()):
        assert tool.policy is Policy.AUTO


@pytest.mark.unit
def test_side_effecting_tools_are_always_ask():
    # draft_email is gated too — it STAGES a send_email approval (never composes-and-drops).
    for tool in (
        DraftEmail(), SendEmail(), UpdateDeal(), UpdateContact(), CreateActivity(), CreateDeal(),
        IssueQuote(),
    ):
        assert tool.policy is Policy.ALWAYS_ASK


@pytest.mark.unit
def test_tool_binds_tenant_before_db_access():
    db = SpyDB()
    ctx = ToolContext(tenant_id="t-123", db=db)
    ReadCrm().invoke(ctx, entity="contacts", limit=10)
    assert db.tenant == "t-123"  # RLS tenant was set before the read


@pytest.mark.unit
def test_send_email_never_sends_routes_to_greenlight():
    gl = InMemoryGreenlight()
    ctx = ToolContext(tenant_id="t-1", agent="nadia", greenlight=gl)
    out = SendEmail().invoke(ctx, to="x@y.com", body="hi", subject="s")
    assert out["status"] == "pending_approval"
    assert out["approval"]["status"] == "pending"
    # The proposal is queued; nothing was sent.
    assert len(gl.queue) == 1
    assert gl.queue[0]["action"] == "send_email"


@pytest.mark.unit
def test_update_deal_and_issue_quote_carry_value_at_stake():
    gl = InMemoryGreenlight()
    ctx = ToolContext(tenant_id="t-1", greenlight=gl)
    UpdateDeal().invoke(ctx, deal_id="d1", changes={"amount": 5000})
    UpdateContact().invoke(ctx, contact_id="c1", changes={"email": "x@y.com"})
    CreateActivity().invoke(ctx, deal_id="d1", kind="note", body="followed up")
    CreateDeal().invoke(ctx, company_id="co1", name="New deal", stage="new", amount=2500)
    IssueQuote().invoke(ctx, deal_id="d1", amount=7500)
    assert gl.queue[0]["value_at_stake"] == 5000
    assert gl.queue[1]["action"] == "update_contact"
    assert gl.queue[2]["action"] == "create_activity"
    assert gl.queue[3]["value_at_stake"] == 2500
    assert gl.queue[4]["value_at_stake"] == 7500


@pytest.mark.unit
def test_always_ask_without_greenlight_still_does_not_execute():
    ctx = ToolContext(tenant_id="t-1")  # no greenlight configured
    out = SendEmail().invoke(ctx, to="x@y.com", body="hi")
    assert out["status"] == "pending_approval"
    assert out["greenlight"] == "unconfigured"
