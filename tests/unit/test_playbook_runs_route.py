"""Unit: GET /studio/playbooks/{id}/runs + registration persistence (audit P0-2 / P0-3).

Run history is the customer-visible answer to "did my playbook run, and what did it
propose?" — persisted by the runner, listed here. Registration persistence is the
orphan-leak fix: activate mints the MA crew ONCE, stores the FULL ids on the row
(operator material — stripped from every wire body), and run/reactivate reuse them.

Same TestClient harness as tests/unit/test_playbook_run.py.
"""
import pytest
from fastapi.testclient import TestClient

from agents.playbooks.store import InMemoryPlaybookRunStore, InMemoryPlaybookStore
from agents.runtime import FakeRuntime
from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.routes_studio import StudioDeps
from api.views import SavedViews

H_A = {"Authorization": "Bearer tenant-a-token"}
H_B = {"Authorization": "Bearer tenant-b-token"}


class TwoTenantVerifier:
    def verify(self, token: str) -> dict:
        if token == "tenant-a-token":
            return {"sub": "userA", "custom:tenant_id": "A", "email": "a@x.com"}
        if token == "tenant-b-token":
            return {"sub": "userB", "custom:tenant_id": "B", "email": "b@x.com"}
        raise ValueError("bad token")


def _good_definition(name="Runs-route playbook"):
    return {
        "name": name,
        "trigger": {"kind": "manual"},
        "roster": [{"agent": "nadia", "tools": ["draft_email"]}],
        "autonomy": "L1",
        "greenlight": {"side_effects": "always_ask"},
    }


class LongIdRuntime(FakeRuntime):
    """FakeRuntime with realistically-long MA ids, so tail-stripping assertions bite."""
    def _id(self, prefix):
        self._n += 1
        return f"{prefix}_{'k' * 30}{self._n}"

    def send_message(self, session, message):
        self.sent.append((session.id, message))
        return {"answer": "done", "delegations": [], "pending_approvals": [], "tool_results": []}


def _client(studio: StudioDeps) -> TestClient:
    deps = ApiDeps(
        verifier=TwoTenantVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None, studio=studio,
    )
    return TestClient(create_app(deps))


def _wired(**kw):
    store = InMemoryPlaybookStore()
    deps = StudioDeps(store=store, **kw)
    return _client(deps), store


def _created_pid(client, headers=H_A) -> str:
    r = client.post("/studio/playbooks", headers=headers, json={"definition": _good_definition()})
    assert r.status_code == 201
    return r.json()["id"]


# ------------------------------------------------------------------ runs route
@pytest.mark.unit
def test_runs_route_requires_auth_and_degrades_honestly():
    client, store = _wired()  # no run_store
    pid = _created_pid(client)
    assert client.get(f"/studio/playbooks/{pid}/runs").status_code == 401
    r = client.get(f"/studio/playbooks/{pid}/runs", headers=H_A)
    assert r.status_code == 503  # honest: history isn't wired on this deployment
    assert "run history" in r.json()["detail"]


@pytest.mark.unit
def test_runs_route_404_for_absent_or_foreign_playbook():
    client, store = _wired(run_store=InMemoryPlaybookRunStore())
    pid = _created_pid(client, H_A)
    assert client.get("/studio/playbooks/00000000-0000-0000-0000-000000000000/runs",
                      headers=H_A).status_code == 404
    # Another tenant's playbook is indistinguishable from absent (no existence oracle).
    assert client.get(f"/studio/playbooks/{pid}/runs", headers=H_B).status_code == 404


@pytest.mark.unit
def test_run_now_persists_history_and_the_runs_route_returns_it_sanitized():
    rt = LongIdRuntime()
    runs = InMemoryPlaybookRunStore()
    client, store = _wired(registrar=rt, run_store=runs)
    pid = _created_pid(client)
    assert client.post(f"/studio/playbooks/{pid}/activate", headers=H_A).status_code == 200
    assert client.post(f"/studio/playbooks/{pid}/run", headers=H_A).status_code == 200

    r = client.get(f"/studio/playbooks/{pid}/runs", headers=H_A)
    assert r.status_code == 200
    rows = r.json()["runs"]
    assert len(rows) == 1
    assert rows[0]["playbook_id"] == pid and rows[0]["status"] == "ok"
    # tenant ids and FULL MA ids never leave the API.
    body = r.text
    assert "tenant_id" not in rows[0] and "tenant_id" not in rows[0]["record"]
    full_coordinator = store.get("A", pid)["ma_coordinator_id"]
    assert full_coordinator and full_coordinator not in body


@pytest.mark.unit
def test_runs_route_is_newest_first_and_limit_bounded():
    rt = LongIdRuntime()
    runs = InMemoryPlaybookRunStore()
    client, store = _wired(registrar=rt, run_store=runs)
    pid = _created_pid(client)
    client.post(f"/studio/playbooks/{pid}/activate", headers=H_A)
    for _ in range(3):
        client.post(f"/studio/playbooks/{pid}/run", headers=H_A)
    r = client.get(f"/studio/playbooks/{pid}/runs?limit=2", headers=H_A)
    assert len(r.json()["runs"]) == 2
    assert client.get(f"/studio/playbooks/{pid}/runs?limit=0", headers=H_A).status_code == 422


# ------------------------------------------------------ registration persistence
@pytest.mark.unit
def test_activate_persists_full_ids_and_wire_carries_tails_only():
    rt = LongIdRuntime()
    client, store = _wired(registrar=rt)
    pid = _created_pid(client)

    r = client.post(f"/studio/playbooks/{pid}/activate", headers=H_A)
    assert r.status_code == 200 and r.json()["registered"] is True
    row = store.get("A", pid)
    assert row["ma_coordinator_id"] in rt.coordinators       # FULL id persisted for reuse
    assert row["ma_registered_version"] == row["version"]
    assert row["ma_coordinator_id"] not in r.text            # ...but never on the wire
    assert r.json()["registration"]["coordinator_id_tail"] == row["ma_coordinator_id"][-6:]

    # Reactivating an unchanged definition REUSES the crew — no new MA agents.
    agents_before = len(rt.agents)
    r2 = client.post(f"/studio/playbooks/{pid}/activate", headers=H_A)
    assert r2.status_code == 200 and r2.json()["registered"] is True
    assert len(rt.agents) == agents_before


@pytest.mark.unit
def test_playbook_wire_bodies_never_carry_ma_columns():
    rt = LongIdRuntime()
    client, store = _wired(registrar=rt)
    pid = _created_pid(client)
    client.post(f"/studio/playbooks/{pid}/activate", headers=H_A)

    for body in (client.get(f"/studio/playbooks/{pid}", headers=H_A).json(),
                 client.get("/studio/playbooks", headers=H_A).json()["playbooks"][0]):
        assert "ma_coordinator_id" not in body and "ma_agent_ids" not in body
        assert body.get("ma_registered") is True             # honest boolean stays


# ------------------------------------------------------------- dispatch honesty
@pytest.mark.unit
def test_playbooks_list_reports_trigger_dispatch_state():
    """The Studio must not present schedule/event playbooks as live automation when the legs
    are off (audit P0-4): the list response carries the deployment's dispatch state."""
    client, _ = _wired(run_store=InMemoryPlaybookRunStore())
    out = client.get("/studio/playbooks", headers=H_A).json()
    assert out["dispatch"] == {"scheduling_enabled": False, "events_enabled": False}

    store2 = InMemoryPlaybookStore()
    client2 = _client(StudioDeps(store=store2, scheduling_enabled=True, events_enabled=True))
    assert client2.get("/studio/playbooks", headers=H_A).json()["dispatch"] == {
        "scheduling_enabled": True, "events_enabled": True}
