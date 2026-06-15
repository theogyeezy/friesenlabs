"""Unit: the orphan-roster reaper (agents/retirement.py).

Every roster auto-upgrade records the roster it superseded in the retired_rosters ledger; the reaper
deletes those Managed-Agents agents after a grace window and marks the ledger row reaped. These pin:
the grace-window filter (pure), and the reap orchestration over a FakeRuntime (dry-run is read-only;
apply deletes specialists + coordinator and marks reaped; a WIN-case retirement with no stored
specialist ids resolves them from MA; a partial delete failure leaves the row due to retry).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agents.retirement import InMemoryRetirementSource, due_retirements, reap_orphans
from agents.runtime import FakeRuntime

NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


def _row(row_id, coordinator_id, agent_ids, *, age_seconds, reaped=False):
    return {
        "id": row_id,
        "tenant_id": "t-1",
        "coordinator_id": coordinator_id,
        "agent_ids": list(agent_ids),
        "retired_at": NOW - timedelta(seconds=age_seconds),
        "reaped_at": NOW if reaped else None,
    }


# ---------------------------------------------------------------- due_retirements (pure filter)
@pytest.mark.unit
def test_due_excludes_rows_inside_the_grace_window():
    rows = [
        _row(1, "coord-A", ["a1"], age_seconds=10_000),   # old -> due
        _row(2, "coord-B", ["b1"], age_seconds=10),        # just retired -> NOT due (grace)
    ]
    due = due_retirements(rows, NOW, grace_seconds=3600)
    assert [r["id"] for r in due] == [1]


@pytest.mark.unit
def test_due_excludes_already_reaped_rows():
    rows = [
        _row(1, "coord-A", ["a1"], age_seconds=10_000, reaped=True),
        _row(2, "coord-B", ["b1"], age_seconds=10_000),
    ]
    due = due_retirements(rows, NOW, grace_seconds=3600)
    assert [r["id"] for r in due] == [2]


# ---------------------------------------------------------------- reap_orphans orchestration
def _seed_roster(rt, n_specialists=2):
    """Mint a real coordinator + specialists on the FakeRuntime; return (coordinator_id, [agent_ids])."""
    agents = [rt.create_agent(None) for _ in range(n_specialists)]
    coord = rt.create_coordinator(_Spec(), agents)
    return coord, agents


class _Spec:
    name = "coordinator"


@pytest.mark.unit
def test_dry_run_deletes_nothing_and_reaps_nothing():
    rt = FakeRuntime()
    coord, agents = _seed_roster(rt)
    src = InMemoryRetirementSource([_row(1, coord, agents, age_seconds=10_000)])

    report = reap_orphans(rt, src, now=NOW, grace_seconds=3600, apply=False)

    assert report["apply"] is False and report["due"] == 1
    assert set(report["rosters"][0]["targets"]) == {coord, *agents}   # what WOULD be deleted
    # nothing actually removed / marked
    assert coord in rt.coordinators and all(a in rt.agents for a in agents)
    assert src.reaped == []


@pytest.mark.unit
def test_apply_deletes_specialists_and_coordinator_then_marks_reaped():
    rt = FakeRuntime()
    coord, agents = _seed_roster(rt)
    src = InMemoryRetirementSource([_row(1, coord, agents, age_seconds=10_000)])

    report = reap_orphans(rt, src, now=NOW, grace_seconds=3600, apply=True)

    assert coord not in rt.coordinators
    assert all(a not in rt.agents for a in agents)
    assert src.reaped == [1]
    assert set(report["rosters"][0]["deleted"]) == {coord, *agents}
    assert report["rosters"][0]["reaped"] is True


@pytest.mark.unit
def test_win_case_resolves_specialists_from_ma_when_not_stored():
    # The WIN path retires the OLD coordinator with an empty agent_ids list (the row only knew the
    # coordinator id) — the reaper must resolve its pinned specialists from the live MA topology.
    rt = FakeRuntime()
    coord, agents = _seed_roster(rt)
    src = InMemoryRetirementSource([_row(1, coord, [], age_seconds=10_000)])  # no stored specialists

    reap_orphans(rt, src, now=NOW, grace_seconds=3600, apply=True)

    assert coord not in rt.coordinators
    assert all(a not in rt.agents for a in agents)   # resolved from MA + deleted
    assert src.reaped == [1]


@pytest.mark.unit
def test_grace_window_protects_a_freshly_retired_roster():
    rt = FakeRuntime()
    coord, agents = _seed_roster(rt)
    src = InMemoryRetirementSource([_row(1, coord, agents, age_seconds=30)])  # within grace

    report = reap_orphans(rt, src, now=NOW, grace_seconds=3600, apply=True)

    assert report["due"] == 0
    assert coord in rt.coordinators            # untouched
    assert src.reaped == []


@pytest.mark.unit
def test_partial_delete_failure_leaves_the_row_due_for_retry():
    coord, agents = "coord-X", ["a1", "a2"]

    class _FlakyRuntime(FakeRuntime):
        def list_agents(self):
            return []
        def delete_agent(self, agent_id):
            if agent_id == "a2":
                raise RuntimeError("MA delete failed")
            # a1 + coord delete fine

    rt = _FlakyRuntime()
    src = InMemoryRetirementSource([_row(1, coord, agents, age_seconds=10_000)])

    report = reap_orphans(rt, src, now=NOW, grace_seconds=3600, apply=True)

    roster = report["rosters"][0]
    assert "a1" in roster["deleted"] and "a2" in roster["failed"]
    assert roster["reaped"] is False
    assert src.reaped == []                    # NOT marked — a later run retries the straggler


@pytest.mark.unit
def test_reaper_survives_an_unlistable_ma_and_still_reaps_stored_ids():
    coord, agents = "coord-Y", ["s1", "s2"]

    class _NoListRuntime(FakeRuntime):
        def list_agents(self):
            raise RuntimeError("MA list unavailable")
        def __init__(self):
            super().__init__()
            self.deleted = []
        def delete_agent(self, agent_id):
            self.deleted.append(agent_id)

    rt = _NoListRuntime()
    src = InMemoryRetirementSource([_row(1, coord, agents, age_seconds=10_000)])

    reap_orphans(rt, src, now=NOW, grace_seconds=3600, apply=True)
    assert set(rt.deleted) == {coord, *agents}   # stored ids still reaped despite no MA listing
    assert src.reaped == [1]
