"""Integration: a Phase 4 side-effecting tool composes with the Phase 5 Greenlight queue.

The control-plane Greenlight satisfies the `Greenlight` protocol that agents/tools/base.py expects, so
an always_ask tool's proposal lands in the real approval queue WITHOUT executing the side effect.
"""
import pytest

from agents.tools.base import ToolContext
from agents.tools.sideeffecting import SendEmail
from api.control.greenlight import Greenlight


@pytest.mark.integration
def test_send_email_tool_routes_to_control_plane_greenlight():
    gl = Greenlight()
    ctx = ToolContext(tenant_id="tenant-A", agent="nadia", greenlight=gl)

    out = SendEmail().invoke(ctx, to="lead@acme.com", subject="Following up", body="hi there")

    # The tool never sent; it queued a pending approval in the control-plane queue.
    assert out["status"] == "pending_approval"
    pending = gl.list_pending("tenant-A")
    assert len(pending) == 1
    assert pending[0]["proposed_action"]["action"] == "send_email"
    assert pending[0]["status"] == "pending"

    # Tenant isolation holds at the queue level too.
    assert gl.list_pending("tenant-B") == []
