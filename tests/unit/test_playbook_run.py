"""Unit: POST /studio/playbooks/{id}/run — the manual 'Run now' trigger endpoint.

Mirrors the style of tests/unit/test_playbook_activation.py and the integration shapes from
tests/integration/test_api_studio.py; exercises the endpoint in isolation via FastAPI's
TestClient over an in-memory store + FakeRuntime (or None registrar).

Cases proven:
  * 503 when the studio store is unconfigured
  * 401 when unauthenticated
  * 404 when the playbook is absent (or another tenant's — same oracle)
  * 409 when the playbook is not active (still a draft)
  * 422 when the stored definition is invalid (re-validation as defense in depth)
  * record-only when no registrar configured: ran=False, run_reason mentions unconfigured
  * a real run through FakeRuntime+StubRuntime: RunRecord serialized with draft actions
    surfaced as status "pending" (NOT executed, NOT auto-approved)
  * tenant comes from the verified claim — a different tenant cannot trigger another's playbook
"""
import pytest
from fastapi.testclient import TestClient

from agents.playbooks.store import InMemoryPlaybookStore
from agents.runtime import FakeRuntime, Session
from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.routes_studio import StudioDeps
from api.views import SavedViews


# --------------------------------------------------------------------------- #
# helpers — mirrors test_api_studio.py conventions
# --------------------------------------------------------------------------- #

H_A = {"Authorization": "Bearer tenant-a-token"}
H_B = {"Authorization": "Bearer tenant-b-token"}


class TwoTenantVerifier:
    def verify(self, token: str) -> dict:
        if token == "tenant-a-token":
            return {"sub": "userA", "custom:tenant_id": "A", "email": "a@x.com"}
        if token == "tenant-b-token":
            return {"sub": "userB", "custom:tenant_id": "B", "email": "b@x.com"}
        raise ValueError("bad token")


def _good_definition(name="Run-test playbook"):
    return {
        "name": name,
        "trigger": {"kind": "manual"},
        "roster": [
            {"agent": "scout", "tools": ["search_rag", "read_crm"]},
            {"agent": "nadia", "tools": ["draft_email"]},
        ],
        "autonomy": "L1",
        "greenlight": {"side_effects": "always_ask"},
    }


def _client(studio: StudioDeps | None = None) -> TestClient:
    deps = ApiDeps(
        verifier=TwoTenantVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
        studio=studio if studio is not None else StudioDeps(),
    )
    return TestClient(create_app(deps))


def _wired(registrar=None):
    """Return (client, store) with a wired InMemoryPlaybookStore and optional registrar."""
    store = InMemoryPlaybookStore()
    deps = StudioDeps(store=store, registrar=registrar)
    return _client(deps), store


def _active_pid(client: TestClient, store: InMemoryPlaybookStore, headers: dict = H_A) -> str:
    """Create + activate a playbook; return its id."""
    r = client.post("/studio/playbooks", headers=headers, json={"definition": _good_definition()})
    assert r.status_code == 201
    pid = r.json()["id"]
    # Activate directly via the store so we do NOT need a registrar for activation in every test.
    store.set_status("A" if headers == H_A else "B", pid, "active")
    return pid


# A fake runtime that echoes a caller-supplied response — the same StubRuntime pattern as
# test_playbook_runner.py, but local to this module to keep tests self-contained.
class StubRuntime(FakeRuntime):
    def __init__(self, response: dict):
        super().__init__()
        self._response = response

    def send_message(self, session: Session, message: str) -> dict:
        self.sent.append((session.id, message))
        return {"session_id": session.id, "tenant_id": session.tenant_id, **self._response}


# A side-effecting tool routed to Greenlight via Tool.invoke (draft-only):
_ROUTED_DRAFT = {
    "status": "pending_approval",
    "tool_name": "send_email",
    "input": {"to": "lead@acme.com", "subject": "Hi", "body": "(draft) Hi"},
    "custom_tool_use_id": "ctu_1",
    "proposal": {"action": "send_email"},
    "approval": {"id": 9, "status": "pending"},
}


# --------------------------------------------------------------------------- #
# 503 — store not configured
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_run_503_when_store_unconfigured():
    client = _client(StudioDeps(store=None))
    r = client.post("/studio/playbooks/any-id/run", headers=H_A)
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# 401 — unauthenticated
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_run_401_unauth():
    client, _ = _wired()
    r = client.post("/studio/playbooks/some-id/run")
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# 404 — absent playbook (and another tenant's = same oracle)
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_run_404_absent():
    client, _ = _wired()
    r = client.post("/studio/playbooks/00000000-0000-0000-0000-000000000000/run", headers=H_A)
    assert r.status_code == 404
    assert "no such playbook" in r.json()["detail"]


@pytest.mark.unit
def test_run_404_another_tenants_playbook():
    """A run request for another tenant's playbook is indistinguishable from absent (no oracle)."""
    client, store = _wired()
    pid = _active_pid(client, store, headers=H_A)
    # Tenant B cannot see tenant A's playbook.
    r = client.post(f"/studio/playbooks/{pid}/run", headers=H_B)
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# 409 — playbook not active
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_run_409_not_active():
    client, _ = _wired()
    r = client.post("/studio/playbooks", headers=H_A, json={"definition": _good_definition()})
    pid = r.json()["id"]  # status is 'draft' — never activated
    r = client.post(f"/studio/playbooks/{pid}/run", headers=H_A)
    assert r.status_code == 409
    assert "not active" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# 422 — re-validation of the STORED definition (defense in depth)
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_run_422_invalid_stored_definition():
    """A stored definition that fails re-validation (e.g. schema tightened since activation)
    must return 422 before anything runs."""
    client, store = _wired()
    pid = _active_pid(client, store)
    # Mutate the stored definition out-of-band to an invalid state (tool not in scout's grant).
    store.rows[pid]["definition"]["roster"][0]["tools"] = ["send_email"]
    r = client.post(f"/studio/playbooks/{pid}/run", headers=H_A)
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# record-only when no registrar configured
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_run_record_only_when_no_registrar():
    """No registrar -> ran=False + run_reason mentioning unconfigured; nothing is fabricated."""
    client, store = _wired(registrar=None)
    pid = _active_pid(client, store)
    r = client.post(f"/studio/playbooks/{pid}/run", headers=H_A)
    assert r.status_code == 200
    body = r.json()
    assert body["ran"] is False
    assert "not configured" in body["run_reason"]
    # Playbook row is unchanged (record-only; status stays active).
    assert body["status"] == "active"


# --------------------------------------------------------------------------- #
# real run — FakeRuntime + StubRuntime, draft actions surfaced as pending
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_run_with_registrar_surfaces_pending_drafts_not_sent():
    """The runner routes the side-effecting call to Greenlight (draft-only); the endpoint
    surfaces it as status 'pending' — NOT 'sent' and NOT auto-approved."""
    rt = StubRuntime({
        "answer": "Drafted email for your approval.",
        "delegations": ["scout", "nadia"],
        "pending_approvals": [dict(_ROUTED_DRAFT)],
        "tool_results": [
            {"tool": "draft_email", "custom_tool_use_id": "ctu_0", "status": "ok"},
            {"tool": "send_email", "custom_tool_use_id": "ctu_1", "status": "queued_for_approval"},
        ],
    })
    client, store = _wired(registrar=rt)
    pid = _active_pid(client, store)

    r = client.post(f"/studio/playbooks/{pid}/run", headers=H_A)
    assert r.status_code == 200
    body = r.json()

    assert body["ran"] is True
    run = body["run"]
    # Draft-only invariant: pending, not ok; nothing auto-approved.
    assert run["status"] == "pending"
    assert run["actions_proposed"] == [_ROUTED_DRAFT]
    assert run["actions_approved"] == [], "a manual trigger must never auto-approve"
    # Playbook id is present; internal tenant_id never leaves the API.
    assert run["playbook_id"] == pid
    assert "tenant_id" not in run
    # run_id is a non-empty string (uuid from the record).
    assert run.get("run_id") and isinstance(run["run_id"], str)


@pytest.mark.unit
def test_run_ok_status_when_no_pending_actions():
    """No side-effecting proposals -> status 'ok'."""
    rt = StubRuntime({"answer": "All set.", "delegations": ["scout"], "tool_results": []})
    client, store = _wired(registrar=rt)
    pid = _active_pid(client, store)

    r = client.post(f"/studio/playbooks/{pid}/run", headers=H_A)
    assert r.status_code == 200
    body = r.json()
    assert body["ran"] is True
    assert body["run"]["status"] == "ok"
    assert body["run"]["actions_proposed"] == []
    assert body["run"]["actions_approved"] == []


@pytest.mark.unit
def test_run_executes_nothing_directly():
    """The endpoint + runner never execute a tool themselves — exactly one send_message is sent
    and the runtime opens exactly one session."""
    rt = StubRuntime({"answer": "done", "delegations": []})
    client, store = _wired(registrar=rt)
    pid = _active_pid(client, store)

    client.post(f"/studio/playbooks/{pid}/run", headers=H_A)

    assert len(rt.sent) == 1, "exactly one send_message per run"
    assert len(rt.sessions) == 1, "exactly one session per run"


# --------------------------------------------------------------------------- #
# THE TRUST RULE — tenant ONLY from the verified claim, never from path/body
# --------------------------------------------------------------------------- #

@pytest.mark.unit
def test_run_tenant_from_claim_not_path_or_body():
    """Tenant B cannot run tenant A's playbook even if it guesses the id."""
    rt = StubRuntime({"answer": "x", "delegations": []})
    client, store = _wired(registrar=rt)
    pid = _active_pid(client, store, headers=H_A)  # owned by tenant A

    # Tenant B's claim -> 404 (A's row is invisible)
    r = client.post(f"/studio/playbooks/{pid}/run", headers=H_B)
    assert r.status_code == 404
    # The runtime was never touched on behalf of B.
    assert rt.sent == [], "no agent work done for a cross-tenant attempt"
