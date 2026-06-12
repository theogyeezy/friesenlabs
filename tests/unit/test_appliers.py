"""Unit: Greenlight applier dispatch."""
import pytest

from api.control.appliers import APPLIERS, apply_approved_action, was_performed


class SpyCrm:
    def __init__(self):
        self.calls = []

    def update_deal_fields(self, **kw):
        self.calls.append(("update_deal_fields", kw))
        return {"id": kw["deal_id"], "updated": kw["changes"]}

    def update_contact_fields(self, **kw):
        self.calls.append(("update_contact_fields", kw))
        return {"id": kw["contact_id"], "updated": kw["changes"]}

    def insert_activity(self, **kw):
        self.calls.append(("insert_activity", kw))
        return {"id": "act-1"}

    def insert_deal(self, **kw):
        self.calls.append(("insert_deal", kw))
        return {"id": "deal-1"}


@pytest.mark.unit
def test_dispatch_map_covers_greenlight_actions():
    assert {
        "update_deal",
        "update_contact",
        "create_activity",
        "create_deal",
        "send_email",
        "issue_quote",
    } <= set(APPLIERS)


@pytest.mark.unit
def test_crm_appliers_call_expected_write_methods():
    crm = SpyCrm()
    assert apply_approved_action(
        crm, "T1", {"action": "update_deal", "deal_id": "d1", "changes": {"stage": "won"}}
    )["performed"]
    assert apply_approved_action(
        crm, "T1", {"action": "update_contact", "contact_id": "c1", "changes": {"email": "x"}}
    )["performed"]
    assert apply_approved_action(
        crm, "T1", {"action": "create_activity", "deal_id": "d1", "kind": "note", "body": "b"}
    )["performed"]
    assert apply_approved_action(
        crm,
        "T1",
        {
            "action": "create_deal",
            "company_id": "co1",
            "name": "Deal",
            "stage": "new",
            "amount": 100,
        },
    )["performed"]
    assert [name for name, _ in crm.calls] == [
        "update_deal_fields",
        "update_contact_fields",
        "insert_activity",
        "insert_deal",
    ]
    assert all(call[1]["tenant_id"] == "T1" for call in crm.calls)


@pytest.mark.unit
def test_send_and_quote_are_record_only():
    assert apply_approved_action(None, "T1", {"action": "send_email"}) == {
        "performed": False,
        "reason": "draft-only until provider go-live",
    }
    assert apply_approved_action(None, "T1", {"action": "issue_quote"}) == {
        "performed": False,
        "reason": "draft-only until provider go-live",
    }


@pytest.mark.unit
def test_unknown_action_raises():
    with pytest.raises(ValueError, match="no applier"):
        apply_approved_action(None, "T1", {"action": "delete_everything"})


@pytest.mark.unit
def test_was_performed_is_the_honesty_signal():
    # A record-only / draft-only send is NOT performed — it must never read as "sent".
    record_only = apply_approved_action(None, "T1", {"action": "send_email"})
    assert was_performed(record_only) is False

    # A real CRM write IS performed.
    crm = SpyCrm()
    real = apply_approved_action(
        crm, "T1", {"action": "update_deal", "deal_id": "d1", "changes": {"stage": "won"}}
    )
    assert was_performed(real) is True

    # Honesty signal is the explicit flag, never the presence of a (truthy) result dict.
    assert was_performed({"performed": False, "error": "ValueError"}) is False
    assert was_performed({}) is False
