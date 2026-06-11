"""Unit: the in-memory playbook store honors the tenant contract + lifecycle semantics.

The InMemoryPlaybookStore mirrors PgPlaybookStore's contract (RLS makes another tenant's row
indistinguishable from absent), so these tests pin the behavior every consumer — and the Pg
twin — must honor. The Pg store itself is proven against real Postgres in
tests/integration/test_playbooks_rls.py.
"""
import pytest

from agents.playbooks.store import InMemoryPlaybookStore

A, B = "tenant-a", "tenant-b"


def defn(name="My playbook"):
    return {
        "name": name,
        "trigger": {"kind": "manual"},
        "roster": [{"agent": "pip"}],
        "autonomy": "L1",
        "greenlight": {"side_effects": "always_ask"},
    }


@pytest.mark.unit
def test_create_and_get_roundtrip():
    s = InMemoryPlaybookStore()
    row = s.create(A, defn(), template_id="t1", created_by="userA")
    assert row["version"] == 1
    assert row["status"] == "draft"
    assert row["template_id"] == "t1"
    assert row["created_by"] == "userA"
    got = s.get(A, row["id"])
    assert got is not None and got["definition"]["name"] == "My playbook"


@pytest.mark.unit
def test_list_is_tenant_scoped():
    s = InMemoryPlaybookStore()
    s.create(A, defn("a1"))
    s.create(A, defn("a2"))
    s.create(B, defn("b1"))
    assert [r["name"] for r in s.list(A)] == ["a1", "a2"]
    assert [r["name"] for r in s.list(B)] == ["b1"]


@pytest.mark.unit
def test_cross_tenant_get_update_delete_are_absent():
    """The RLS contract: tenant B touching tenant A's row behaves exactly like the row
    not existing — no read, no write, no delete, no existence oracle."""
    s = InMemoryPlaybookStore()
    row = s.create(A, defn())
    assert s.get(B, row["id"]) is None
    assert s.update_definition(B, row["id"], defn("stolen")) is None
    assert s.set_status(B, row["id"], "active") is None
    assert s.delete(B, row["id"]) is False
    # A's row is untouched.
    mine = s.get(A, row["id"])
    assert mine["definition"]["name"] == "My playbook"
    assert mine["status"] == "draft"


@pytest.mark.unit
def test_update_bumps_version():
    s = InMemoryPlaybookStore()
    row = s.create(A, defn())
    updated = s.update_definition(A, row["id"], defn("renamed"))
    assert updated["version"] == 2
    assert updated["name"] == "renamed"
    assert s.update_definition(A, row["id"], defn("again"))["version"] == 3


@pytest.mark.unit
def test_set_status_validates():
    s = InMemoryPlaybookStore()
    row = s.create(A, defn())
    assert s.set_status(A, row["id"], "active")["status"] == "active"
    assert s.set_status(A, row["id"], "draft")["status"] == "draft"
    with pytest.raises(ValueError):
        s.set_status(A, row["id"], "yolo")


@pytest.mark.unit
def test_delete():
    s = InMemoryPlaybookStore()
    row = s.create(A, defn())
    assert s.delete(A, row["id"]) is True
    assert s.get(A, row["id"]) is None
    assert s.delete(A, row["id"]) is False


@pytest.mark.unit
def test_rows_are_copies():
    s = InMemoryPlaybookStore()
    row = s.create(A, defn())
    row["definition"]["name"] = "mutated externally"
    assert s.get(A, row["id"])["definition"]["name"] == "My playbook"
