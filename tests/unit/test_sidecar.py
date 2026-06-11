"""Unit: the Sidecar suggestion engine (api/sidecar.py) — pure, deterministic, grounded."""
from datetime import datetime, timedelta, timezone

import pytest

from api import sidecar as S

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _deal(**kw):
    base = {"id": "d1", "title": "Acme", "stage": "new", "amount": 5000,
            "contact_id": "c1", "created_at": NOW}
    base.update(kw)
    return base


def _contact(**kw):
    base = {"id": "c1", "name": "Dana", "email": "d@x.com", "phone": "555",
            "last_activity_at": NOW}
    base.update(kw)
    return base


@pytest.mark.unit
def test_closed_deals_are_skipped():
    deals = [_deal(stage="closed_won"), _deal(id="d2", stage="lost")]
    out = S.build_suggestions(deals, [], now=NOW)
    assert out["suggestions"] == [] and out["total"] == 0


@pytest.mark.unit
def test_unlinked_open_deal_suggests_attach_contact():
    out = S.build_suggestions([_deal(contact_id=None)], [], now=NOW)
    s = out["suggestions"][0]
    assert s["kind"] == "unlinked_deal"
    assert s["action"]["action"] == "create_activity" and s["action"]["deal_id"] == "d1"
    assert s["action"]["kind"] == "task"


@pytest.mark.unit
def test_aging_open_deal_suggests_followup():
    old = _deal(created_at=NOW - timedelta(days=20))
    s = S.build_suggestions([old], [], now=NOW)["suggestions"][0]
    assert s["kind"] == "aging_open_deal"
    assert s["value_at_stake"] == 5000.0
    assert s["action"] == {"action": "create_activity", "deal_id": "d1", "kind": "follow_up",
                           "body": 'Follow up on “Acme” (open 20 days).'}


@pytest.mark.unit
def test_fresh_linked_deal_yields_nothing():
    # young (created today) + has a contact -> no deal suggestion.
    out = S.build_suggestions([_deal(created_at=NOW)], [], now=NOW)
    assert all(x["entity_type"] != "deal" for x in out["suggestions"])


@pytest.mark.unit
def test_unreachable_contact_suggests_enrich():
    s = S.build_suggestions([], [_contact(email=None, phone=None)], now=NOW)["suggestions"][0]
    assert s["kind"] == "missing_contact_info"
    assert s["action"]["action"] == "create_activity" and s["action"]["contact_id"] == "c1"


@pytest.mark.unit
def test_stale_contact_suggests_reconnect():
    stale = _contact(last_activity_at=NOW - timedelta(days=45))
    s = S.build_suggestions([], [stale], now=NOW)["suggestions"][0]
    assert s["kind"] == "stale_contact" and s["action"]["kind"] == "follow_up"


@pytest.mark.unit
def test_never_active_contact_is_stale():
    s = S.build_suggestions([], [_contact(last_activity_at=None)], now=NOW)["suggestions"][0]
    assert s["kind"] == "stale_contact"
    assert "ever" in s["detail"]


@pytest.mark.unit
def test_recent_full_contact_yields_nothing():
    out = S.build_suggestions([], [_contact()], now=NOW)
    assert out["suggestions"] == []


@pytest.mark.unit
def test_deterministic_and_priority_ordered():
    deals = [_deal(id="d2", contact_id=None, amount=10), _deal(id="d3", created_at=NOW - timedelta(days=30), amount=99999)]
    a = S.build_suggestions(deals, [], now=NOW)["suggestions"]
    b = S.build_suggestions(deals, [], now=NOW)["suggestions"]
    assert [x["id"] for x in a] == [x["id"] for x in b]   # deterministic
    # unlinked deal is boosted above aging deals regardless of amount.
    assert a[0]["kind"] == "unlinked_deal"
    # internal sort key never leaks.
    assert all("priority" not in x for x in a)


@pytest.mark.unit
def test_truncation_reports_true_total():
    deals = [_deal(id=f"d{i}", contact_id=None) for i in range(25)]
    out = S.build_suggestions(deals, [], now=NOW, limit=10)
    assert len(out["suggestions"]) == 10 and out["total"] == 25 and out["truncated"] is True
