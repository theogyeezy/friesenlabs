"""Playbook run history + MA-registration persistence (audit P0-2 / P0-3).

The run store persists each RunRecord digest so a tenant can see "did my playbook run,
and what did it propose?" (previously the digest was logged or returned once, then gone).
The registration columns persist the MA crew ids minted at activation so the runner can
REUSE the coordinator instead of re-creating agents on every run (the orphan leak).

Contract mirrors PlaybookStore: every method is keyed by tenant_id first; another
tenant's rows are indistinguishable from absent (the RLS contract, in memory).
"""
from __future__ import annotations

import pytest

from agents.playbooks.store import InMemoryPlaybookRunStore, InMemoryPlaybookStore


def _run(playbook_id: str, *, status: str = "ok", run_id: str | None = None) -> dict:
    return {
        "run_id": run_id or f"r-{playbook_id}-{status}",
        "playbook_id": playbook_id,
        "status": status,
        "trigger": {"kind": "manual", "name": "run-now"},
        "answer": "did the thing",
        "actions_proposed": [],
        "trace": [{"event": "triggered"}],
    }


# --------------------------------------------------------------------------- runs
@pytest.mark.unit
def test_record_persists_and_list_returns_newest_first():
    store = InMemoryPlaybookRunStore()
    a = store.record("t-1", _run("p-1", run_id="r-a"))
    b = store.record("t-1", _run("p-1", run_id="r-b"))
    assert a["id"] and a["tenant_id"] == "t-1" and a["created_at"]
    runs = store.list("t-1")
    assert [r["run_id"] for r in runs] == [b["run_id"], a["run_id"]]  # newest first


@pytest.mark.unit
def test_list_filters_by_playbook_and_honors_limit():
    store = InMemoryPlaybookRunStore()
    for i in range(5):
        store.record("t-1", _run("p-1", run_id=f"r-{i}"))
    store.record("t-1", _run("p-2", run_id="r-other"))
    assert {r["playbook_id"] for r in store.list("t-1", "p-1")} == {"p-1"}
    assert len(store.list("t-1", "p-1", limit=2)) == 2
    assert [r["run_id"] for r in store.list("t-1", "p-1", limit=2)] == ["r-4", "r-3"]


@pytest.mark.unit
def test_runs_are_tenant_scoped_and_copies_are_defensive():
    store = InMemoryPlaybookRunStore()
    store.record("t-1", _run("p-1"))
    assert store.list("t-2") == []          # another tenant's runs are absent
    assert store.list("t-2", "p-1") == []
    row = store.list("t-1")[0]
    row["status"] = "tampered"
    assert store.list("t-1")[0]["status"] == "ok"  # mutation never reaches the store


# ----------------------------------------------------------------- registration
@pytest.mark.unit
def test_set_registration_persists_ma_ids_and_version():
    store = InMemoryPlaybookStore()
    row = store.create("t-1", {"name": "pb"})
    out = store.set_registration("t-1", row["id"], coordinator_id="coord_123",
                                 agent_ids=["ag_1", "ag_2"], version=row["version"])
    assert out["ma_coordinator_id"] == "coord_123"
    assert out["ma_agent_ids"] == ["ag_1", "ag_2"]
    assert out["ma_registered_version"] == row["version"]
    assert store.get("t-1", row["id"])["ma_coordinator_id"] == "coord_123"


@pytest.mark.unit
def test_set_registration_is_tenant_scoped():
    store = InMemoryPlaybookStore()
    row = store.create("t-1", {"name": "pb"})
    assert store.set_registration("t-2", row["id"], coordinator_id="c",
                                  agent_ids=[], version=1) is None
    assert store.get("t-1", row["id"]).get("ma_coordinator_id") is None


@pytest.mark.unit
def test_definition_update_bumps_version_past_the_registration():
    # The reuse check is `ma_registered_version == version`; an edit must invalidate it
    # WITHOUT the store having to know about registrations (version bump is the seam).
    store = InMemoryPlaybookStore()
    row = store.create("t-1", {"name": "pb"})
    store.set_registration("t-1", row["id"], coordinator_id="c", agent_ids=["a"],
                           version=row["version"])
    updated = store.update_definition("t-1", row["id"], {"name": "pb2"})
    assert updated["ma_registered_version"] == row["version"]
    assert updated["version"] == row["version"] + 1  # stale by construction
