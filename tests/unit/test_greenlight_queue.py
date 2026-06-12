"""Unit: the Greenlight queue — propose, list, approve/edit/deny transitions."""
import pytest

from api.control.greenlight import EditNotAllowed, Greenlight, InMemoryApprovalStore


def _propose(gl, tenant="t1"):
    return gl.propose(tenant_id=tenant, action="send_email", agent="nadia",
                      reasoning="follow up on hot lead", value_at_stake=2500.0,
                      payload={"to": "x@y.com", "subject": "Hi", "body": "draft"})


@pytest.mark.unit
def test_propose_then_list_pending():
    gl = Greenlight()
    rec = _propose(gl)
    assert rec["status"] == "pending"
    assert rec["value_at_stake"] == 2500.0
    pend = gl.list_pending("t1")
    assert len(pend) == 1 and pend[0]["id"] == rec["id"]


@pytest.mark.unit
def test_approve():
    gl = Greenlight()
    rec = _propose(gl)
    out = gl.decide("t1", rec["id"], "approve", decided_by="matt")
    assert out["status"] == "approved" and out["decided_by"] == "matt"
    assert gl.list_pending("t1") == []


@pytest.mark.unit
def test_edit_changes_what_would_execute():
    gl = Greenlight()
    rec = _propose(gl)
    out = gl.decide("t1", rec["id"], "edit", edits={"body": "edited by human"})
    assert out["status"] == "approved"
    assert out["proposed_action"]["body"] == "edited by human"


@pytest.mark.unit
def test_deny_carries_message():
    gl = Greenlight()
    rec = _propose(gl)
    out = gl.decide("t1", rec["id"], "deny", deny_message="not now")
    assert out["status"] == "denied" and out["deny_message"] == "not now"


@pytest.mark.unit
def test_cannot_decide_twice():
    gl = Greenlight()
    rec = _propose(gl)
    gl.decide("t1", rec["id"], "approve")
    with pytest.raises(ValueError):
        gl.decide("t1", rec["id"], "deny")


@pytest.mark.unit
def test_inmemory_conditional_update_is_check_and_set():
    # The atomic pending->decided arbiter: expected_status gates the write and the rowcount-style
    # return tells the caller whether IT won the transition.
    store = InMemoryApprovalStore()
    aid = store.insert({"tenant_id": "t1", "proposed_action": {"action": "send_email"},
                        "status": "pending", "agent": "nadia", "reasoning": "r", "value_at_stake": 1})
    assert store.update("t1", aid, {"status": "approved"}, expected_status="pending") == 1
    # Second conditional write loses: the row is no longer pending.
    assert store.update("t1", aid, {"status": "denied"}, expected_status="pending") == 0
    assert store.get("t1", aid)["status"] == "approved"
    # Unconditional updates (the audit path) still touch the decided row.
    assert store.update("t1", aid, {"apply_result": {"performed": True}}) == 1


@pytest.mark.unit
def test_decide_raises_for_the_race_loser_without_double_deciding():
    # Simulate the TOCTOU window: the loser's read sees a stale 'pending' snapshot, but the
    # conditional update returns 0 — decide() must raise, never report a second win.
    class StaleReadStore(InMemoryApprovalStore):
        def __init__(self):
            super().__init__()
            self.stale_pending_reads = 0

        def get(self, tenant_id, approval_id):
            row = super().get(tenant_id, approval_id)
            if row is not None and self.stale_pending_reads > 0:
                self.stale_pending_reads -= 1
                return {**row, "status": "pending"}  # the stale snapshot the loser raced on
            return row

    store = StaleReadStore()
    gl = Greenlight(store=store)
    rec = _propose(gl)
    assert gl.decide("t1", rec["id"], "approve", decided_by="winner")["status"] == "approved"
    store.stale_pending_reads = 1  # the loser's pre-write read still says pending
    # The loser's error re-reads the row and names where it actually landed ("already approved").
    with pytest.raises(ValueError, match="already approved"):
        gl.decide("t1", rec["id"], "deny", decided_by="loser")
    out = gl.store.get("t1", rec["id"])
    assert out["status"] == "approved" and out["decided_by"] == "winner"


@pytest.mark.unit
def test_edit_cannot_change_the_action_key():
    gl = Greenlight()
    rec = _propose(gl)
    with pytest.raises(EditNotAllowed):
        gl.decide("t1", rec["id"], "edit", edits={"action": "create_deal"})
    # The guard fires BEFORE the status flip: the approval is still pending, untouched.
    row = gl.store.get("t1", rec["id"])
    assert row["status"] == "pending"
    assert row["proposed_action"]["action"] == "send_email"


@pytest.mark.unit
def test_edit_cannot_introduce_keys_outside_the_payload():
    gl = Greenlight()
    rec = _propose(gl)
    with pytest.raises(EditNotAllowed, match="'bcc'"):
        gl.decide("t1", rec["id"], "edit", edits={"bcc": "evil@x.com"})
    assert gl.store.get("t1", rec["id"])["status"] == "pending"


@pytest.mark.unit
def test_tenant_scoped_listing():
    gl = Greenlight()
    _propose(gl, tenant="t1")
    _propose(gl, tenant="t2")
    assert len(gl.list_pending("t1")) == 1
    assert len(gl.list_pending("t2")) == 1


@pytest.mark.unit
def test_ma_confirmation_mapping():
    gl = Greenlight()
    rec = _propose(gl)
    approved = gl.decide("t1", rec["id"], "approve")
    ev = gl.to_ma_confirmation(approved, tool_use_id="tu_1")
    assert ev["result"] == "allow" and ev["type"] == "user.tool_confirmation"


@pytest.mark.unit
def test_payload_action_key_cannot_override_the_registry_name():
    """The trusted, registry-derived action name must win over a smuggled
    payload['action'] — it is the applier-dispatch discriminator and the label
    compliance/traces key off (audit-label divergence + compliance route-around
    otherwise)."""
    gl = Greenlight()
    rec = gl.propose(
        tenant_id="t1",
        action="send_email",                      # what the registry/gate derived
        agent="a1",
        reasoning="r",
        value_at_stake=None,
        payload={"action": "update_deal",          # the smuggle attempt
                 "deal_id": "d1", "changes": {"stage": "closed_won"},
                 "to": "x@example.com", "body": "hi"},
    )
    assert rec["proposed_action"]["action"] == "send_email"
