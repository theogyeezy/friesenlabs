"""Seeded demo approvals must be APPLIER-SHAPED (found live 2026-06-12).

The original seeds were display-shaped: `update_deal` carried `deal`/`field` (no
`deal_id`/`changes`) so approving it KeyError'd in the applier; `send_email` carried
`body_preview` (no `body`) so the CAN-SPAM decide-time check could never pass AND the
edit guard (correctly) refused adding the novel `body` key — permanently un-approvable.

These tests run every seeded payload through the REAL decide-path machinery:
the compliance choke point (`Greenlight._compliance_verdict` — the exact pre-apply
validation) and the real appliers (`apply_approved_action`). A reshaped seed that
drifts back to display-only fails here, not in front of a customer.
"""
from __future__ import annotations

import uuid

import pytest

from api.control.appliers import apply_approved_action
from api.control.greenlight import Greenlight
from scripts.seed_demo_tenant import build_demo_approvals

DIDS = {  # deal title -> id, as main() builds it from the inserted rows
    "Birchwood platform expansion": str(uuid.uuid4()),
    "Halcyon fleet rollout": str(uuid.uuid4()),
    "Mesa Verde clinic suite": str(uuid.uuid4()),
}


class _FakeCrm:
    def __init__(self):
        self.updates: list[tuple] = []

    def update_deal_fields(self, *, tenant_id, deal_id, changes):
        self.updates.append((tenant_id, deal_id, changes))
        return {"id": deal_id, **changes}


def _approvals():
    return build_demo_approvals(DIDS)


@pytest.mark.unit
def test_seed_payloads_pass_the_decide_time_compliance_choke_point():
    gl = Greenlight()
    for payload, agent, _reasoning, value in _approvals():
        verdict = gl._compliance_verdict(
            action=payload["action"], agent=agent, tenant_id="t-demo",
            value_at_stake=value, proposed_action=dict(payload),
        )
        assert verdict.ok, f"{payload['action']} seed fails compliance: {verdict.reason}"


@pytest.mark.unit
def test_seed_send_email_has_a_real_body_with_an_unsubscribe_mechanism():
    sends = [p for p, *_ in _approvals() if p["action"] == "send_email"]
    assert sends, "the demo seed should include a send_email draft"
    for p in sends:
        assert p.get("body"), "send_email seeds must carry a FULL body (not body_preview)"
        assert "unsubscribe" in p["body"].lower(), "CAN-SPAM: the seed body needs an unsubscribe line"
        assert "body_preview" not in p, "display-shaped key must not come back"


@pytest.mark.unit
def test_seed_update_deal_applies_through_the_real_applier():
    crm = _FakeCrm()
    updates = [p for p, *_ in _approvals() if p["action"] == "update_deal"]
    assert updates, "the demo seed should include an update_deal draft"
    for p in updates:
        result = apply_approved_action(crm, "t-demo", dict(p),
                                       approval_id="a-1", decided_by="u-1")
        assert result["performed"] is True, f"applier did not perform: {result}"
        assert p["deal_id"] in DIDS.values(), "deal_id must be a REAL seeded deal id"
        assert crm.updates[-1][2], "changes must be a non-empty dict"


@pytest.mark.unit
def test_seed_record_only_actions_apply_without_error():
    # send_email / issue_quote ride the record_only applier until provider go-live —
    # approving a seed must yield the honest performed:false, never an exception.
    for p, *_ in _approvals():
        if p["action"] in ("send_email", "issue_quote"):
            result = apply_approved_action(None, "t-demo", dict(p),
                                           approval_id="a-2", decided_by="u-1")
            assert result["performed"] is False
            assert "draft-only" in (result.get("reason") or "")


@pytest.mark.unit
def test_seed_module_is_import_safe():
    # Importing the module must never read env / boto3 / connect to a DB — the side
    # effects live in main(). (This test importing build_demo_approvals at the top is
    # itself the proof; assert the guard exists for drift protection.)
    import inspect

    import scripts.seed_demo_tenant as mod
    src = inspect.getsource(mod)
    assert 'if __name__ == "__main__"' in src
    assert hasattr(mod, "main")
