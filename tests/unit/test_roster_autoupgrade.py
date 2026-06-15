"""Unit: self-upgrading rosters (agents/provisioning.py).

A tenant's Managed-Agents specialists + coordinator are created ONCE with the code's specs frozen
in; a later spec change never reaches them. These tests pin the version-stamp + lazy auto-upgrade
that fixes it: the stamp bumps on ANY spec change, and a stale tenant re-provisions transparently on
its next conversation build — locked per tenant, best-effort, never breaking the turn.
"""
from __future__ import annotations

import threading

import pytest

import agents.provisioning as prov
from agents.provisioning import (
    current_roster_version,
    maybe_upgrade_roster,
    provision_roster,
)
from agents.runtime import FakeRuntime
from agents.workspace_store import InMemoryWorkspaceStore


@pytest.fixture(autouse=True)
def _reset_version_cache():
    # current_roster_version() memoizes per process; reset so a monkeypatched spec is re-hashed.
    prov._cached_version = None
    yield
    prov._cached_version = None


def _seed(store, tenant, *, version):
    store.upsert(tenant, "ws-1", "env-1", "coord-OLD", roster_version=version)


@pytest.mark.unit
def test_version_is_deterministic_and_prefixed():
    v1 = current_roster_version()
    prov._cached_version = None
    v2 = current_roster_version()
    assert v1 == v2
    assert v1.startswith("rv1-")


@pytest.mark.unit
def test_version_changes_when_a_spec_changes(monkeypatch):
    base = current_roster_version()

    # Change one specialist's model -> a different stamp.
    from agents import roster as roster_mod
    specs = roster_mod.roster()
    monkeypatch.setattr(specs[1], "model", roster_mod.OPUS)
    monkeypatch.setattr(roster_mod, "roster", lambda: specs)
    prov._cached_version = None
    assert current_roster_version() != base


@pytest.mark.unit
def test_version_changes_when_a_tool_schema_changes(monkeypatch):
    base = current_roster_version()
    # A change to a granted tool's input_schema must bump the stamp (the exact 2026-06-14 case:
    # draft_email's schema changed but tenants kept the old one).
    from agents.tools.sideeffecting import DraftEmail
    new_schema = {**DraftEmail.input_schema,
                  "properties": {**DraftEmail.input_schema["properties"], "cc": {"type": "string"}}}
    monkeypatch.setattr(DraftEmail, "input_schema", new_schema)
    prov._cached_version = None
    assert current_roster_version() != base


@pytest.mark.unit
def test_provision_roster_creates_roster_and_stamps_version():
    rt, store = FakeRuntime(), InMemoryWorkspaceStore()
    rt.create_environment("env")
    out = provision_roster(rt, store, "t-1", environment_id="env-1", workspace_id="ws-1")

    assert out["coordinator_id"] in rt.coordinators           # a real coordinator was created
    assert len(rt.coordinators[out["coordinator_id"]]) == 7   # pinning all 7 specialists
    row = store.get("t-1")
    assert row["coordinator_id"] == out["coordinator_id"]
    assert row["roster_version"] == current_roster_version()
    assert row["session_id"] is None                          # stale session cleared


@pytest.mark.unit
def test_upgrade_is_a_noop_when_the_stamp_is_current():
    rt, store = FakeRuntime(), InMemoryWorkspaceStore()
    _seed(store, "t-1", version=current_roster_version())
    row = store.get("t-1")
    cid = maybe_upgrade_roster(rt, store, row, "t-1")
    assert cid == "coord-OLD"          # unchanged
    assert rt.coordinators == {}       # nothing created


@pytest.mark.unit
@pytest.mark.parametrize("stale", ["rv1-deadbeefdeadbeef", None])
def test_upgrade_reprovisions_when_stale_or_unstamped(stale):
    rt, store = FakeRuntime(), InMemoryWorkspaceStore()
    _seed(store, "t-1", version=stale)
    row = store.get("t-1")

    cid = maybe_upgrade_roster(rt, store, row, "t-1")

    assert cid != "coord-OLD"                       # a fresh coordinator is now in use
    assert cid in rt.coordinators
    after = store.get("t-1")
    assert after["coordinator_id"] == cid
    assert after["roster_version"] == current_roster_version()


@pytest.mark.unit
def test_upgrade_falls_back_to_existing_coordinator_on_failure():
    store = InMemoryWorkspaceStore()
    _seed(store, "t-1", version="rv1-stale")
    row = store.get("t-1")

    class _BoomRuntime(FakeRuntime):
        def create_agent(self, spec):
            raise RuntimeError("MA create failed")

    cid = maybe_upgrade_roster(_BoomRuntime(), store, row, "t-1")
    assert cid == "coord-OLD"                                   # degrade, never break the turn
    assert store.get("t-1")["coordinator_id"] == "coord-OLD"    # store untouched on failure


@pytest.mark.unit
def test_concurrent_upgrades_provision_exactly_once():
    rt, store = FakeRuntime(), InMemoryWorkspaceStore()
    _seed(store, "t-1", version=None)
    row = store.get("t-1")

    results, barrier = [], threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(maybe_upgrade_roster(rt, store, row, "t-1"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All callers converge on ONE coordinator, and only one roster was actually created.
    assert len(set(results)) == 1
    assert len(rt.coordinators) == 1
