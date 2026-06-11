"""Unit: the playbook trigger dispatcher (agents/playbooks/dispatch.py) — cron matching,
ACTIVE-only firing, schedule vs event selection, and containment. Offline: fake store + runner."""
from datetime import datetime, timezone

import pytest

from agents.playbooks import STATUS_ACTIVE, STATUS_DRAFT
from agents.playbooks.dispatch import PlaybookDispatcher, cron_due
from agents.playbooks.runner import RunRecord, TriggerEvent


# --------------------------------------------------------------------------- cron_due
@pytest.mark.unit
@pytest.mark.parametrize("expr,dt,expected", [
    ("* * * * *", datetime(2026, 6, 11, 13, 30, tzinfo=timezone.utc), True),
    ("30 13 * * *", datetime(2026, 6, 11, 13, 30, tzinfo=timezone.utc), True),
    ("30 13 * * *", datetime(2026, 6, 11, 13, 31, tzinfo=timezone.utc), False),  # minute miss
    ("0 13 * * 1-5", datetime(2026, 6, 11, 13, 0, tzinfo=timezone.utc), True),   # Thu in Mon-Fri
    ("0 13 * * 1-5", datetime(2026, 6, 13, 13, 0, tzinfo=timezone.utc), False),  # Sat
    ("0 14 * * 2", datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc), True),      # Tue
    ("0 14 * * 0", datetime(2026, 6, 14, 14, 0, tzinfo=timezone.utc), True),     # Sun=0
    ("*/15 * * * *", datetime(2026, 6, 11, 9, 45, tzinfo=timezone.utc), True),   # step
    ("*/15 * * * *", datetime(2026, 6, 11, 9, 46, tzinfo=timezone.utc), False),
    ("0 0 1 * *", datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc), True),        # day-of-month
    ("0 0 1 * *", datetime(2026, 6, 2, 0, 0, tzinfo=timezone.utc), False),
    ("bad cron", datetime(2026, 6, 11, 13, 30, tzinfo=timezone.utc), False),     # malformed
    ("99 * * * *", datetime(2026, 6, 11, 13, 30, tzinfo=timezone.utc), False),   # out of range
])
def test_cron_due(expr, dt, expected):
    assert cron_due(expr, dt) is expected


# --------------------------------------------------------------------------- fakes
class FakeStore:
    def __init__(self, rows):
        self._rows = rows  # list of {id, status, definition}

    def list(self, tenant_id):
        return list(self._rows)


def _row(pid, status, trigger):
    return {"id": pid, "status": status, "definition": {"trigger": trigger, "name": pid}}


def _runner():
    """A run_playbook that records calls and returns an ok RunRecord."""
    calls = []

    def run_playbook(tenant_id, playbook_id, event: TriggerEvent) -> RunRecord:
        calls.append((tenant_id, playbook_id, event))
        return RunRecord(playbook_id=playbook_id, tenant_id=tenant_id, status="ok",
                         trigger={"kind": event.kind, "name": event.name})
    return run_playbook, calls


# --------------------------------------------------------------------------- dispatch_scheduled
@pytest.mark.unit
def test_scheduled_runs_only_active_due_schedule_playbooks():
    now = datetime(2026, 6, 11, 13, 0, tzinfo=timezone.utc)  # Thu 13:00
    rows = [
        _row("due", STATUS_ACTIVE, {"kind": "schedule", "schedule": "0 13 * * 1-5"}),  # fires
        _row("not_due", STATUS_ACTIVE, {"kind": "schedule", "schedule": "0 9 * * *"}),  # wrong hour
        _row("draft", STATUS_DRAFT, {"kind": "schedule", "schedule": "0 13 * * 1-5"}),  # not active
        _row("event", STATUS_ACTIVE, {"kind": "event", "event": "lead.created"}),       # not schedule
    ]
    run_playbook, calls = _runner()
    out = PlaybookDispatcher(FakeStore(rows), run_playbook).dispatch_scheduled("T1", now=now)
    assert [r.playbook_id for r in out] == ["due"]
    assert len(calls) == 1
    assert calls[0][2].kind == "schedule"


@pytest.mark.unit
def test_scheduled_empty_when_nothing_due():
    now = datetime(2026, 6, 11, 3, 0, tzinfo=timezone.utc)
    rows = [_row("p", STATUS_ACTIVE, {"kind": "schedule", "schedule": "0 13 * * *"})]
    run_playbook, calls = _runner()
    out = PlaybookDispatcher(FakeStore(rows), run_playbook).dispatch_scheduled("T1", now=now)
    assert out == [] and calls == []


# --------------------------------------------------------------------------- dispatch_event
@pytest.mark.unit
def test_event_runs_only_matching_active_event_playbooks():
    rows = [
        _row("match", STATUS_ACTIVE, {"kind": "event", "event": "lead.created"}),
        _row("other_event", STATUS_ACTIVE, {"kind": "event", "event": "deal.won"}),
        _row("draft", STATUS_DRAFT, {"kind": "event", "event": "lead.created"}),
        _row("sched", STATUS_ACTIVE, {"kind": "schedule", "schedule": "* * * * *"}),
    ]
    run_playbook, calls = _runner()
    out = PlaybookDispatcher(FakeStore(rows), run_playbook).dispatch_event(
        "T1", "lead.created", payload={"id": "L1"})
    assert [r.playbook_id for r in out] == ["match"]
    assert calls[0][2].kind == "event"
    assert calls[0][2].payload == {"id": "L1"}


@pytest.mark.unit
def test_event_no_match_runs_nothing():
    rows = [_row("p", STATUS_ACTIVE, {"kind": "event", "event": "deal.won"})]
    run_playbook, calls = _runner()
    out = PlaybookDispatcher(FakeStore(rows), run_playbook).dispatch_event("T1", "lead.created")
    assert out == [] and calls == []


@pytest.mark.unit
def test_module_is_import_safe():
    # Importing built nothing live; main is callable and the CLI requires a mode.
    from agents.playbooks import dispatch
    assert dispatch.main(["--tenant", "x"]) == 2  # no --schedule -> usage error, no DB touched
