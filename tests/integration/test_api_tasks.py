"""Integration: /tasks endpoints — the real CRM tasks/reminders surface (CRM-depth #14).

Proves the api half of the tasks vertical slice (shapes mirror test_api_contacts.py):
  * 401 unauth on every route (the shared current_tenant dependency)
  * tenant ALWAYS from the verified claim — a smuggled tenant_id in the body is IGNORED
    (THE TRUST RULE); the fake records the claim tenant on every call
  * GET /tasks lists scoped tasks (open/overdue/done/all/archived); a junk scope is 422;
    the open/overdue counts ride along for the nav badge
  * POST /tasks creates a task; blank/overlong title -> 422; a contact_id/deal_id link to a
    missing/other-tenant row -> 404 (existence checked before the FK)
  * complete/reopen flip done_at; archive/unarchive soft-delete; missing id -> 404 everywhere
  * PATCH edits title/due_at; empty body -> 422
  * internal tenant_id never leaves the API; a leaked cross-tenant row fails loud
  * unconfigured deps (no DSN -> no client) answer honest 503s; the default ApiDeps mounts
    the routes with the honest stub (never 404 / fake success)
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.tasks_routes import MAX_TITLE_LEN, TasksDeps
from api.views import SavedViews

H = {"Authorization": "Bearer t"}

TASK_A1 = "11111111-1111-1111-1111-111111111111"
TASK_A2 = "22222222-2222-2222-2222-222222222222"
TASK_B1 = "99999999-9999-9999-9999-999999999999"
CONTACT_A1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
DEAL_A1 = "dddddddd-dddd-dddd-dddd-dddddddddddd"
MISSING = "ffffffff-ffff-ffff-ffff-ffffffffffff"


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}


def _task(tid, tenant, title, *, due_at=None, done=False, overdue=False, archived=False,
          contact_id=None, deal_id=None):
    return {
        "id": tid, "tenant_id": tenant, "title": title, "due_at": due_at,
        "done_at": "2026-06-10T00:00:00+00:00" if done else None, "done": done,
        "overdue": overdue, "archived_at": "2026-06-09T00:00:00+00:00" if archived else None,
        "contact_id": contact_id, "deal_id": deal_id, "contact_name": None,
        "deal_title": None, "created_by": "uA", "created_at": "2026-06-01T00:00:00+00:00",
    }


class FakeTaskCrm:
    """In-memory PgCrmClient-shaped task client. Honors the RLS contract (a read for tenant A
    never surfaces tenant B's rows) and records every call so tests can assert the claim tenant
    steered it and the smuggled body tenant was ignored. Tasks are keyed by tenant; the existence
    checks for links (get_contact_directory/get_deal_board) consult small per-tenant id sets."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.tasks = {
            "A": [
                _task(TASK_A1, "A", "Call back Birchwood", due_at="2026-06-01T00:00:00+00:00",
                      overdue=True, contact_id=CONTACT_A1),
                _task(TASK_A2, "A", "Send proposal", due_at="2026-12-01T00:00:00+00:00"),
            ],
            "B": [_task(TASK_B1, "B", "B-only secret task")],
        }
        self.contacts = {"A": {CONTACT_A1}, "B": set()}
        self.deals = {"A": {DEAL_A1}, "B": set()}

    # reads ----------------------------------------------------------------
    def list_tasks(self, *, tenant_id, scope="open", contact_id=None, deal_id=None,
                   limit=50, offset=0):
        self.calls.append(("list_tasks", tenant_id, scope, contact_id, deal_id, limit, offset))
        rows = [dict(r) for r in self.tasks.get(tenant_id, [])]
        if scope == "archived":
            rows = [r for r in rows if r["archived_at"] is not None]
        else:
            rows = [r for r in rows if r["archived_at"] is None]
            if scope == "open":
                rows = [r for r in rows if not r["done"]]
            elif scope == "overdue":
                rows = [r for r in rows if not r["done"] and r["overdue"]]
            elif scope == "done":
                rows = [r for r in rows if r["done"]]
        if contact_id:
            rows = [r for r in rows if r["contact_id"] == contact_id]
        if deal_id:
            rows = [r for r in rows if r["deal_id"] == deal_id]
        return rows[offset:offset + limit]

    def count_open_tasks(self, *, tenant_id):
        self.calls.append(("count_open", tenant_id))
        rows = [r for r in self.tasks.get(tenant_id, []) if r["archived_at"] is None]
        return {
            "open_count": sum(1 for r in rows if not r["done"]),
            "overdue_count": sum(1 for r in rows if not r["done"] and r["overdue"]),
        }

    def get_task(self, *, tenant_id, task_id):
        self.calls.append(("get_task", tenant_id, task_id))
        for r in self.tasks.get(tenant_id, []):
            if r["id"] == task_id:
                return dict(r)
        return None

    def get_contact_directory(self, *, tenant_id, contact_id):
        self.calls.append(("get_contact", tenant_id, contact_id))
        return {"id": contact_id, "tenant_id": tenant_id} \
            if contact_id in self.contacts.get(tenant_id, set()) else None

    def get_deal_board(self, *, tenant_id, deal_id):
        self.calls.append(("get_deal", tenant_id, deal_id))
        return {"id": deal_id, "tenant_id": tenant_id} \
            if deal_id in self.deals.get(tenant_id, set()) else None

    # writes ---------------------------------------------------------------
    def insert_task(self, *, tenant_id, title, due_at=None, contact_id=None, deal_id=None,
                    created_by=None):
        self.calls.append(("insert_task", tenant_id, title, due_at, contact_id, deal_id,
                           created_by))
        row = _task("new-task-id", tenant_id, title, due_at=due_at, contact_id=contact_id,
                    deal_id=deal_id)
        self.tasks.setdefault(tenant_id, []).append(row)
        return dict(row)

    def update_task_fields(self, *, tenant_id, task_id, changes):
        self.calls.append(("update_task", tenant_id, task_id, changes))
        for r in self.tasks.get(tenant_id, []):
            if r["id"] == task_id:
                r.update(changes)
                return {"id": task_id, "updated": dict(changes), "task": dict(r)}
        raise ValueError("task not found or not visible")

    def set_task_done(self, *, tenant_id, task_id, done):
        self.calls.append(("set_done", tenant_id, task_id, done))
        for r in self.tasks.get(tenant_id, []):
            if r["id"] == task_id:
                r["done"] = done
                r["done_at"] = "2026-06-11T00:00:00+00:00" if done else None
                return dict(r)
        raise ValueError("task not found or not visible")

    def set_archived(self, *, tenant_id, table, entity_id, archived):
        self.calls.append(("set_archived", tenant_id, table, entity_id, archived))
        assert table == "tasks"
        for r in self.tasks.get(tenant_id, []):
            if r["id"] == entity_id:
                r["archived_at"] = "2026-06-12T00:00:00+00:00" if archived else None
                return {"id": entity_id, "archived": archived, "archived_at": r["archived_at"]}
        raise ValueError("tasks row not found or not visible")


def _client(tasks=None):
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
        tasks=tasks if tasks is not None else TasksDeps(),
    )
    return TestClient(create_app(deps))


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_unauth_401_on_all_routes():
    client = _client(TasksDeps(crm=FakeTaskCrm()))
    assert client.get("/tasks").status_code == 401
    assert client.get(f"/tasks/{TASK_A1}").status_code == 401
    assert client.post("/tasks", json={"title": "x"}).status_code == 401
    assert client.post(f"/tasks/{TASK_A1}/complete").status_code == 401
    assert client.post(f"/tasks/{TASK_A1}/archive").status_code == 401


# --------------------------------------------------------------------------- #
# honest unconfigured stubs
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_unconfigured_503_never_fake_rows():
    client = _client(TasksDeps(crm=None))
    for r in (client.get("/tasks", headers=H),
              client.get(f"/tasks/{TASK_A1}", headers=H),
              client.post("/tasks", json={"title": "x"}, headers=H),
              client.post(f"/tasks/{TASK_A1}/complete", headers=H)):
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"]


@pytest.mark.integration
def test_default_apideps_mounts_routes_with_honest_stub():
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
    )
    client = TestClient(create_app(deps))
    r = client.get("/tasks", headers=H)
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_list_open_tasks_scoped_to_claim_tenant_with_counts():
    crm = FakeTaskCrm()
    client = _client(TasksDeps(crm=crm))
    r = client.get("/tasks", headers=H)
    assert r.status_code == 200
    body = r.json()
    ids = {t["id"] for t in body["tasks"]}
    assert ids == {TASK_A1, TASK_A2}      # both open, tenant A only
    assert TASK_B1 not in ids
    assert body["open_count"] == 2 and body["overdue_count"] == 1
    assert body["scope"] == "open"
    # tenant came from the claim ('A'), not the request
    assert all(c[1] == "A" for c in crm.calls if c[0] == "list_tasks")


@pytest.mark.integration
def test_list_overdue_and_archived_scopes():
    client = _client(TasksDeps(crm=FakeTaskCrm()))
    overdue = client.get("/tasks?scope=overdue", headers=H).json()["tasks"]
    assert {t["id"] for t in overdue} == {TASK_A1}
    archived = client.get("/tasks?scope=archived", headers=H).json()["tasks"]
    assert archived == []   # none archived yet


@pytest.mark.integration
def test_list_junk_scope_is_422():
    client = _client(TasksDeps(crm=FakeTaskCrm()))
    assert client.get("/tasks?scope=bogus", headers=H).status_code == 422


@pytest.mark.integration
def test_internal_tenant_id_never_leaves_on_list():
    client = _client(TasksDeps(crm=FakeTaskCrm()))
    for t in client.get("/tasks", headers=H).json()["tasks"]:
        assert "tenant_id" not in t


# --------------------------------------------------------------------------- #
# create (THE TRUST RULE)
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_create_task_uses_claim_tenant_ignores_body_tenant():
    crm = FakeTaskCrm()
    client = _client(TasksDeps(crm=crm))
    r = client.post("/tasks", json={"title": "Follow up", "tenant_id": "B"}, headers=H)
    assert r.status_code == 201
    assert r.json()["task"]["title"] == "Follow up"
    insert = [c for c in crm.calls if c[0] == "insert_task"][0]
    assert insert[1] == "A"   # claim tenant, NOT the smuggled "B"


@pytest.mark.integration
def test_create_blank_title_422():
    client = _client(TasksDeps(crm=FakeTaskCrm()))
    assert client.post("/tasks", json={"title": "   "}, headers=H).status_code == 422


@pytest.mark.integration
def test_create_overlong_title_422():
    client = _client(TasksDeps(crm=FakeTaskCrm()))
    r = client.post("/tasks", json={"title": "x" * (MAX_TITLE_LEN + 1)}, headers=H)
    assert r.status_code == 422


@pytest.mark.integration
def test_create_with_valid_links_201():
    client = _client(TasksDeps(crm=FakeTaskCrm()))
    r = client.post("/tasks", json={"title": "Call", "contact_id": CONTACT_A1,
                                    "deal_id": DEAL_A1}, headers=H)
    assert r.status_code == 201


@pytest.mark.integration
def test_create_with_missing_contact_link_404():
    client = _client(TasksDeps(crm=FakeTaskCrm()))
    r = client.post("/tasks", json={"title": "Call", "contact_id": MISSING}, headers=H)
    assert r.status_code == 404


@pytest.mark.integration
def test_create_link_to_other_tenant_row_404():
    # CONTACT_A1 belongs to tenant A; the claim IS tenant A so it resolves — prove a deal id that
    # exists only in another tenant reads as missing. TASK_B1's tenant has empty deal/contact sets.
    crm = FakeTaskCrm()
    crm.deals["A"] = set()   # no deals visible to A
    client = _client(TasksDeps(crm=crm))
    r = client.post("/tasks", json={"title": "Call", "deal_id": DEAL_A1}, headers=H)
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# get / edit / complete / archive
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_get_task_cross_tenant_is_404():
    client = _client(TasksDeps(crm=FakeTaskCrm()))
    assert client.get(f"/tasks/{TASK_B1}", headers=H).status_code == 404   # tenant B's task
    assert client.get(f"/tasks/{MISSING}", headers=H).status_code == 404


@pytest.mark.integration
def test_get_malformed_id_404():
    client = _client(TasksDeps(crm=FakeTaskCrm()))
    assert client.get("/tasks/not-a-uuid", headers=H).status_code == 404


@pytest.mark.integration
def test_edit_title_and_due_at():
    crm = FakeTaskCrm()
    client = _client(TasksDeps(crm=crm))
    r = client.patch(f"/tasks/{TASK_A1}", json={"title": "Renamed"}, headers=H)
    assert r.status_code == 200
    assert r.json()["task"]["title"] == "Renamed"


@pytest.mark.integration
def test_edit_empty_body_422():
    client = _client(TasksDeps(crm=FakeTaskCrm()))
    assert client.patch(f"/tasks/{TASK_A1}", json={}, headers=H).status_code == 422


@pytest.mark.integration
def test_edit_missing_task_404():
    client = _client(TasksDeps(crm=FakeTaskCrm()))
    assert client.patch(f"/tasks/{MISSING}", json={"title": "x"}, headers=H).status_code == 404


@pytest.mark.integration
def test_complete_then_reopen():
    crm = FakeTaskCrm()
    client = _client(TasksDeps(crm=crm))
    done = client.post(f"/tasks/{TASK_A1}/complete", headers=H)
    assert done.status_code == 200 and done.json()["task"]["done"] is True
    reopened = client.post(f"/tasks/{TASK_A1}/reopen", headers=H)
    assert reopened.status_code == 200 and reopened.json()["task"]["done"] is False


@pytest.mark.integration
def test_complete_missing_404():
    client = _client(TasksDeps(crm=FakeTaskCrm()))
    assert client.post(f"/tasks/{MISSING}/complete", headers=H).status_code == 404


@pytest.mark.integration
def test_archive_then_unarchive():
    crm = FakeTaskCrm()
    client = _client(TasksDeps(crm=crm))
    a = client.post(f"/tasks/{TASK_A1}/archive", headers=H)
    assert a.status_code == 200 and a.json()["archived"] is True
    u = client.post(f"/tasks/{TASK_A1}/unarchive", headers=H)
    assert u.status_code == 200 and u.json()["archived"] is False


@pytest.mark.integration
def test_archive_missing_404():
    client = _client(TasksDeps(crm=FakeTaskCrm()))
    assert client.post(f"/tasks/{MISSING}/archive", headers=H).status_code == 404
