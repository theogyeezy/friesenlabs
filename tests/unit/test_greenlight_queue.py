"""Unit: the Greenlight queue — propose, list, approve/edit/deny transitions."""
import pytest

from api.control.greenlight import Greenlight


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
    out = gl.decide(rec["id"], "approve", decided_by="matt")
    assert out["status"] == "approved" and out["decided_by"] == "matt"
    assert gl.list_pending("t1") == []


@pytest.mark.unit
def test_edit_changes_what_would_execute():
    gl = Greenlight()
    rec = _propose(gl)
    out = gl.decide(rec["id"], "edit", edits={"body": "edited by human"})
    assert out["status"] == "approved"
    assert out["proposed_action"]["body"] == "edited by human"


@pytest.mark.unit
def test_deny_carries_message():
    gl = Greenlight()
    rec = _propose(gl)
    out = gl.decide(rec["id"], "deny", deny_message="not now")
    assert out["status"] == "denied" and out["deny_message"] == "not now"


@pytest.mark.unit
def test_cannot_decide_twice():
    gl = Greenlight()
    rec = _propose(gl)
    gl.decide(rec["id"], "approve")
    with pytest.raises(ValueError):
        gl.decide(rec["id"], "deny")


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
    approved = gl.decide(rec["id"], "approve")
    ev = gl.to_ma_confirmation(approved, tool_use_id="tu_1")
    assert ev["result"] == "allow" and ev["type"] == "user.tool_confirmation"
