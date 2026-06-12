"""Unit: Greenlight approval expiry + pending-queue pagination (customer-readiness audit P0).

Expiry is LAZY: propose() stamps expires_at; an expired row is excluded from the pending list
and a decide() on it flips it to status='expired' and raises — no sweeper required for safety.
"""
from datetime import datetime, timedelta, timezone

import pytest

from api.control.greenlight import Greenlight


def _propose(gl, tenant="t1", body="draft"):
    return gl.propose(tenant_id=tenant, action="send_email", agent="nadia",
                      reasoning="follow up on hot lead", value_at_stake=2500.0,
                      payload={"to": "x@y.com", "subject": "Hi", "body": body,
                               # satisfies the compliance floor (send_email needs an
                               # unsubscribe mechanism or it lands DENIED, not pending)
                               "has_unsubscribe": True})


def _backdate(gl, tenant, approval_id, seconds=1):
    past = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    assert gl.store.update(tenant, approval_id, {"expires_at": past}) == 1


# --------------------------------------------------------------------------- expiry

@pytest.mark.unit
def test_propose_stamps_expires_at_from_ttl():
    gl = Greenlight(ttl_hours=1)
    before = datetime.now(timezone.utc)
    rec = _propose(gl)
    assert rec["expires_at"] is not None
    delta = rec["expires_at"] - before
    assert timedelta(minutes=59) < delta <= timedelta(hours=1, seconds=5)


@pytest.mark.unit
def test_default_ttl_is_seven_days():
    gl = Greenlight()
    rec = _propose(gl)
    delta = rec["expires_at"] - datetime.now(timezone.utc)
    assert timedelta(days=6, hours=23) < delta <= timedelta(days=7, seconds=5)


@pytest.mark.unit
def test_decide_on_expired_flips_to_expired_and_raises():
    gl = Greenlight(ttl_hours=1)
    rec = _propose(gl)
    _backdate(gl, "t1", rec["id"])
    with pytest.raises(ValueError, match="expired"):
        gl.decide("t1", rec["id"], "approve", decided_by="matt")
    assert gl.store.get("t1", rec["id"])["status"] == "expired"


@pytest.mark.unit
def test_expired_rows_are_excluded_from_list_pending():
    gl = Greenlight(ttl_hours=1)
    fresh = _propose(gl, body="fresh")
    stale = _propose(gl, body="stale")
    _backdate(gl, "t1", stale["id"])
    pend = gl.list_pending("t1")
    assert [r["id"] for r in pend] == [fresh["id"]]


# --------------------------------------------------------------------------- pagination

@pytest.mark.unit
def test_page_pending_walks_the_queue_with_a_cursor():
    gl = Greenlight()
    ids = [_propose(gl, body=f"d{i}")["id"] for i in range(5)]
    rows1, cur1 = gl.page_pending("t1", limit=2)
    assert cur1 is not None
    rows2, cur2 = gl.page_pending("t1", limit=2, cursor=cur1)
    assert cur2 is not None
    rows3, cur3 = gl.page_pending("t1", limit=2, cursor=cur2)
    assert cur3 is None
    assert [r["id"] for r in [*rows1, *rows2, *rows3]] == ids


@pytest.mark.unit
def test_page_pending_exact_boundary_has_no_cursor():
    gl = Greenlight()
    for i in range(3):
        _propose(gl, body=f"d{i}")
    rows, cur = gl.page_pending("t1", limit=3)
    assert len(rows) == 3 and cur is None


@pytest.mark.unit
def test_page_pending_is_tenant_scoped():
    gl = Greenlight()
    mine = _propose(gl, tenant="t1")
    _propose(gl, tenant="t2")
    rows, _ = gl.page_pending("t1", limit=10)
    assert [r["id"] for r in rows] == [mine["id"]]


@pytest.mark.unit
def test_page_pending_invalid_cursor_raises_value_error():
    gl = Greenlight()
    _propose(gl)
    with pytest.raises(ValueError):
        gl.page_pending("t1", limit=2, cursor="not-a-cursor")


@pytest.mark.unit
def test_count_pending_excludes_decided_and_expired():
    gl = Greenlight(ttl_hours=1)
    keep = [_propose(gl, body=f"d{i}") for i in range(3)]
    decided = _propose(gl, body="decided")
    stale = _propose(gl, body="stale")
    gl.decide("t1", decided["id"], "deny")
    _backdate(gl, "t1", stale["id"])
    assert gl.count_pending("t1") == len(keep)


# --------------------------------------------------------------------------- error messages

@pytest.mark.unit
def test_already_decided_error_names_the_actual_status():
    gl = Greenlight()
    rec = _propose(gl)
    gl.decide("t1", rec["id"], "approve")
    with pytest.raises(ValueError, match="already approved"):
        gl.decide("t1", rec["id"], "deny")
