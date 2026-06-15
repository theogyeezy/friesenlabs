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
    # Also clear the per-tenant failure backoff so tests don't leak state into each other.
    prov._cached_version = None
    prov._failed_upgrade.clear()
    yield
    prov._cached_version = None
    prov._failed_upgrade.clear()


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


# --------------------------------------------------------------------------------------------
# B1 regression (adversarial review, 2026-06-14): after an upgrade the conversation factory MUST
# start a FRESH session against the new coordinator — NOT resume the tenant's persisted session,
# which is server-side pinned to the OLD coordinator (resume is network-free), or the tenant keeps
# talking to the stale agents and the now-current stamp suppresses any retry. Covers the live
# multi-conversation path (#354), whose session lives in conversation_store.

from agents.runtime import Session


class _UpgradingRuntime:
    """A non-FakeRuntime (so the factory's upgrade path runs) that records resume vs create."""
    def __init__(self):
        self.tool_context_factory = None
        self.resumed: list = []
        self.created_sessions: list = []
        self._n = 0

    def create_agent(self, spec):
        self._n += 1
        return f"agent-new-{self._n}"

    def create_coordinator(self, spec, agent_ids):
        return "coord-NEW"

    def create_session(self, coordinator_id, tenant_id, vault_id=None, environment_id=None):
        self.created_sessions.append(coordinator_id)
        return Session(id="sess-fresh", tenant_id=tenant_id, coordinator_id=coordinator_id)

    def resume_session(self, session_id, coordinator_id, tenant_id, vault_id=None,
                       environment_id=None):
        self.resumed.append((session_id, coordinator_id))
        return Session(id=session_id, tenant_id=tenant_id, coordinator_id=coordinator_id)


class _ConvStore:
    def __init__(self, rows):
        self.rows = rows  # {(tenant, conv): {"session_id": ...}}
    def get(self, tenant, conv):
        return self.rows.get((tenant, conv))
    def set_session_id(self, tenant, conv, sid):
        self.rows[(tenant, conv)]["session_id"] = sid


@pytest.mark.unit
def test_factory_starts_a_fresh_session_after_upgrade_not_resume_the_stale_one():
    from api.asgi import make_conversation_factory

    rt = _UpgradingRuntime()
    store = InMemoryWorkspaceStore()
    store.upsert("t-1", "ws", "env", "coord-OLD", roster_version="rv1-STALE")  # stale tenant
    conv_store = _ConvStore({("t-1", "c-1"): {"session_id": "sess-OLD"}})       # stale thread session

    factory = make_conversation_factory(
        workspace_store=store, runtime_factory=lambda row: rt, conversation_store=conv_store)
    convo = factory("t-1", conversation_id="c-1")

    # The roster was upgraded to the new coordinator...
    assert store.get("t-1")["coordinator_id"] == "coord-NEW"
    assert store.get("t-1")["roster_version"] == current_roster_version()
    assert convo.coordinator_id == "coord-NEW"
    # ...and the turn ran on a FRESH session against it — the stale session was NOT resumed.
    assert rt.resumed == []
    assert rt.created_sessions == ["coord-NEW"]
    # The stale per-conversation session is gone — replaced by the fresh session bound to the new
    # coordinator (it was nulled before construction, then the fresh id persisted back). Either way
    # it is no longer "sess-OLD", so a later turn can never resume the old coordinator's session.
    assert conv_store.get("t-1", "c-1")["session_id"] == "sess-fresh"


class _CountingBoomRuntime(FakeRuntime):
    """A runtime whose agent creation always fails, counting attempts."""
    def __init__(self):
        super().__init__()
        self.create_attempts = 0

    def create_agent(self, spec):
        self.create_attempts += 1
        raise RuntimeError("MA create failed")


@pytest.mark.unit
def test_a_persistent_failure_backs_off_instead_of_re_minting_every_turn():
    rt, store = _CountingBoomRuntime(), InMemoryWorkspaceStore()
    _seed(store, "t-1", version="rv1-stale")
    row = store.get("t-1")

    # Three consecutive turns while provisioning keeps failing.
    for _ in range(3):
        assert maybe_upgrade_roster(rt, store, row, "t-1") == "coord-OLD"
    # Only the FIRST turn actually hit MA — the backoff suppressed the next two.
    assert rt.create_attempts == 1


@pytest.mark.unit
def test_backoff_expires_and_a_later_turn_retries():
    store = InMemoryWorkspaceStore()
    _seed(store, "t-1", version="rv1-stale")
    row = store.get("t-1")
    rt = _CountingBoomRuntime()

    maybe_upgrade_roster(rt, store, row, "t-1")           # fails, arms the cooldown
    assert rt.create_attempts == 1
    # Pretend the cooldown elapsed.
    ver, _ts = prov._failed_upgrade["t-1"]
    prov._failed_upgrade["t-1"] = (ver, prov.time.monotonic() - prov._UPGRADE_BACKOFF_SECONDS - 1)

    maybe_upgrade_roster(rt, store, row, "t-1")           # retries after backoff
    assert rt.create_attempts == 2


@pytest.mark.unit
def test_a_successful_upgrade_clears_the_backoff():
    store = InMemoryWorkspaceStore()
    _seed(store, "t-1", version=None)
    prov._failed_upgrade["t-1"] = ("rv1-stale-other", prov.time.monotonic())  # leftover for old ver
    row = store.get("t-1")
    cid = maybe_upgrade_roster(FakeRuntime(), store, row, "t-1")
    assert cid != "coord-OLD"
    assert "t-1" not in prov._failed_upgrade


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
