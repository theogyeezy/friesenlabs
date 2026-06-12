"""Unit: per-workspace vault isolation — the playbook vault_id persists + reaches the session.

Before this, every playbook run created its session with vault_id=None (the store never carried
a vault_id and the runner only had a constructor fallback that callers left unset). These pin:
  * the store persists + returns vault_id (create + set_vault_id), tenant-scoped;
  * the runner reads the PLAYBOOK ROW's vault_id into create_session (row wins);
  * the constructor vault_id is the FALLBACK when the row has none (back-compat);
  * a row with a vault_id overrides the constructor fallback (row is source of truth).
"""
import pytest

from agents.playbooks import run
from agents.playbooks.store import InMemoryPlaybookStore
from agents.runtime import FakeRuntime


def _defn():
    return {
        "name": "Welcome new leads",
        "description": "Greet and qualify a freshly-created lead.",
        "trigger": {"kind": "event", "event": "lead.created"},
        "roster": [{"agent": "scout", "tools": ["read_crm"]}],
        "autonomy": "L1",
        "greenlight": {"side_effects": "always_ask"},
    }


def _active(store, tenant="tenant-a", *, vault_id=None):
    row = store.create(tenant, _defn(), vault_id=vault_id)
    store.set_status(tenant, row["id"], "active")
    return row["id"]


def _session_vault(rt: FakeRuntime) -> str | None:
    assert len(rt.sessions) == 1
    return next(iter(rt.sessions.values())).metadata["vault_id"]


# ---------------- store persistence ----------------
@pytest.mark.unit
def test_create_persists_and_returns_vault_id():
    store = InMemoryPlaybookStore()
    row = store.create("tenant-a", _defn(), vault_id="vault-xyz")
    assert row["vault_id"] == "vault-xyz"
    assert store.get("tenant-a", row["id"])["vault_id"] == "vault-xyz"


@pytest.mark.unit
def test_create_defaults_vault_id_to_none():
    row = InMemoryPlaybookStore().create("tenant-a", _defn())
    assert row["vault_id"] is None


@pytest.mark.unit
def test_set_vault_id_updates_the_row():
    store = InMemoryPlaybookStore()
    pid = _active(store)
    updated = store.set_vault_id("tenant-a", pid, "vault-123")
    assert updated["vault_id"] == "vault-123"
    assert store.get("tenant-a", pid)["vault_id"] == "vault-123"


@pytest.mark.unit
def test_set_vault_id_is_tenant_scoped():
    store = InMemoryPlaybookStore()
    pid = _active(store, "tenant-a")
    # Another tenant can't see (or mutate) the row — RLS-equivalent: indistinguishable from absent.
    assert store.set_vault_id("tenant-b", pid, "vault-evil") is None
    assert store.get("tenant-a", pid)["vault_id"] is None


# ---------------- the runner reads it into the session ----------------
@pytest.mark.unit
def test_runner_uses_the_rows_vault_id_for_the_session():
    store = InMemoryPlaybookStore()
    pid = _active(store, vault_id="vault-from-row")
    rt = FakeRuntime()
    rec = run(rt, store, "tenant-a", pid, {"kind": "event", "name": "lead.created"})
    assert rec.status in ("ok", "pending")  # ran (not error/not_found)
    assert _session_vault(rt) == "vault-from-row"


@pytest.mark.unit
def test_row_vault_id_overrides_the_constructor_fallback():
    store = InMemoryPlaybookStore()
    pid = _active(store, vault_id="vault-from-row")
    rt = FakeRuntime()
    # Even with a different constructor vault_id, the persisted row wins (source of truth).
    run(rt, store, "tenant-a", pid, {"kind": "event", "name": "lead.created"},
        vault_id="vault-constructor")
    assert _session_vault(rt) == "vault-from-row"


@pytest.mark.unit
def test_constructor_vault_id_is_the_fallback_when_row_has_none():
    store = InMemoryPlaybookStore()
    pid = _active(store)  # no vault_id on the row
    rt = FakeRuntime()
    run(rt, store, "tenant-a", pid, {"kind": "event", "name": "lead.created"},
        vault_id="vault-fallback")
    assert _session_vault(rt) == "vault-fallback"


@pytest.mark.unit
def test_no_vault_anywhere_is_still_none():
    store = InMemoryPlaybookStore()
    pid = _active(store)
    rt = FakeRuntime()
    run(rt, store, "tenant-a", pid, {"kind": "event", "name": "lead.created"})
    assert _session_vault(rt) is None
