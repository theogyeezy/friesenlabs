"""Integration: /conversations endpoints + /chat transcript persistence (multi-thread chat history).

Proves the api half of the conversations vertical slice (shapes mirror test_api_tasks.py):
  * 401 unauth on every route (the shared current_tenant dependency)
  * tenant ALWAYS from the verified claim — a smuggled tenant_id in the body is IGNORED (THE
    TRUST RULE); the fake records the claim tenant on every call
  * GET /conversations lists active/archived (newest first via the store); junk scope -> 422
  * POST /conversations creates a thread (optional title; overlong -> 422)
  * PATCH renames (blank -> 422; missing id -> 404); archive/unarchive flip archived_at
  * GET /conversations/{id}/messages returns the transcript; missing id -> 404
  * unconfigured deps (store None) answer honest 503s; the default ApiDeps mounts the routes
  * /chat with a conversation_id persists a 'user' row up front + an 'agent' row when settled;
    an unknown conversation_id -> 404; conversation_id=None never touches the store (back-compat)
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.conversations_routes import ConversationsDeps, MAX_TITLE_LEN
from api.views import SavedViews

H = {"Authorization": "Bearer t"}
CONV_A1 = "11111111-1111-1111-1111-111111111111"
CONV_B1 = "99999999-9999-9999-9999-999999999999"
MISSING = "ffffffff-ffff-ffff-ffff-ffffffffffff"


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}


class FakeConversationStore:
    """In-memory PgConversationStore-shaped store. Honors RLS (tenant A never sees B's rows) and
    records calls so tests assert the claim tenant steered it. Curated rows carry no tenant_id."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.rows = {
            "A": {CONV_A1: {"id": CONV_A1, "title": "Pipeline questions", "session_id": "sess-A1",
                            "archived_at": None, "created_at": "2026-06-01T00:00:00+00:00",
                            "updated_at": "2026-06-10T00:00:00+00:00"}},
            "B": {CONV_B1: {"id": CONV_B1, "title": "B-only", "session_id": None,
                            "archived_at": None, "created_at": "2026-06-01T00:00:00+00:00",
                            "updated_at": "2026-06-02T00:00:00+00:00"}},
        }
        self.messages: dict[str, list] = {CONV_A1: [
            {"id": "m1", "role": "user", "content": "what's my pipeline?", "citations": [],
             "grounding_status": None, "created_at": "2026-06-10T00:00:00+00:00"},
            {"id": "m2", "role": "agent", "content": "Here is your pipeline.",
             "citations": [{"claim": "x", "source_ref": "deal:1", "snippet": "s"}],
             "grounding_status": "grounded", "created_at": "2026-06-10T00:00:01+00:00"},
        ]}
        self.appended: list[dict] = []

    def _strip(self, row):
        return {k: v for k, v in row.items() if k != "session_id"}

    def list(self, *, tenant_id, scope="active", limit=50, offset=0):
        self.calls.append(("list", tenant_id, scope))
        rows = list(self.rows.get(tenant_id, {}).values())
        want_archived = scope == "archived"
        rows = [self._strip(r) for r in rows if (r["archived_at"] is not None) == want_archived]
        return rows[offset:offset + limit]

    def create(self, *, tenant_id, title=None, created_by=None):
        self.calls.append(("create", tenant_id, title, created_by))
        new = {"id": "new-conv", "title": title, "session_id": None, "archived_at": None,
               "created_at": "2026-06-14T00:00:00+00:00", "updated_at": "2026-06-14T00:00:00+00:00"}
        self.rows.setdefault(tenant_id, {})["new-conv"] = new
        return self._strip(new)

    def get(self, tenant_id, conversation_id):
        self.calls.append(("get", tenant_id, conversation_id))
        row = self.rows.get(tenant_id, {}).get(conversation_id)
        return self._strip(row) if row else None

    def rename(self, *, tenant_id, conversation_id, title):
        self.calls.append(("rename", tenant_id, conversation_id, title))
        row = self.rows.get(tenant_id, {}).get(conversation_id)
        if row is None:
            return None
        row["title"] = title
        return self._strip(row)

    def set_archived(self, *, tenant_id, conversation_id, archived):
        self.calls.append(("set_archived", tenant_id, conversation_id, archived))
        row = self.rows.get(tenant_id, {}).get(conversation_id)
        if row is None:
            return None
        row["archived_at"] = "2026-06-14T00:00:00+00:00" if archived else None
        return self._strip(row)

    def list_messages(self, *, tenant_id, conversation_id, limit=200, offset=0):
        self.calls.append(("list_messages", tenant_id, conversation_id))
        return list(self.messages.get(conversation_id, []))[offset:offset + limit]

    def append_message(self, *, tenant_id, conversation_id, role, content,
                       citations=None, grounding_status=None):
        self.appended.append({"tenant_id": tenant_id, "conversation_id": conversation_id,
                              "role": role, "content": content, "citations": citations or [],
                              "grounding_status": grounding_status})


def _client(store=None, conversation_factory=None):
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=conversation_factory or (lambda t, c=None: None),
        autonomy_config=AutonomyConfig(), executor=lambda a: None,
        conversations=ConversationsDeps(store=store) if store is not None else ConversationsDeps(),
    )
    return TestClient(create_app(deps))


# --------------------------------------------------------------------------- auth
@pytest.mark.integration
def test_unauth_401_on_all_routes():
    client = _client(FakeConversationStore())
    assert client.get("/conversations").status_code == 401
    assert client.post("/conversations", json={}).status_code == 401
    assert client.patch(f"/conversations/{CONV_A1}", json={"title": "x"}).status_code == 401
    assert client.get(f"/conversations/{CONV_A1}/messages").status_code == 401
    assert client.post(f"/conversations/{CONV_A1}/archive").status_code == 401


# --------------------------------------------------------------------------- 503 unconfigured
@pytest.mark.integration
def test_503_when_store_unconfigured():
    client = _client()  # default ConversationsDeps() -> store None
    assert client.get("/conversations", headers=H).status_code == 503
    assert client.post("/conversations", headers=H, json={}).status_code == 503


# --------------------------------------------------------------------------- list / create
@pytest.mark.integration
def test_list_active_and_create():
    store = FakeConversationStore()
    client = _client(store)
    r = client.get("/conversations", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "active"
    assert [c["id"] for c in body["conversations"]] == [CONV_A1]
    # the curated rows never leak the internal session_id or tenant_id
    assert "session_id" not in body["conversations"][0]
    assert "tenant_id" not in body["conversations"][0]
    # junk scope -> 422
    assert client.get("/conversations?scope=bogus", headers=H).status_code == 422
    # create with a smuggled tenant_id is ignored (trust rule): the claim tenant 'A' steers it
    r2 = client.post("/conversations", headers=H, json={"title": "New thread", "tenant_id": "B"})
    assert r2.status_code == 201
    assert ("create", "A", "New thread", "uA") in store.calls
    # overlong title -> 422
    assert client.post("/conversations", headers=H,
                       json={"title": "x" * (MAX_TITLE_LEN + 1)}).status_code == 422


# --------------------------------------------------------------------------- rename / archive
@pytest.mark.integration
def test_rename_and_archive():
    store = FakeConversationStore()
    client = _client(store)
    r = client.patch(f"/conversations/{CONV_A1}", headers=H, json={"title": "Renamed"})
    assert r.status_code == 200 and r.json()["conversation"]["title"] == "Renamed"
    # blank title -> 422
    assert client.patch(f"/conversations/{CONV_A1}", headers=H, json={"title": "  "}).status_code == 422
    # missing id -> 404 (RLS-scoped store returns None)
    assert client.patch(f"/conversations/{MISSING}", headers=H, json={"title": "x"}).status_code == 404
    # another tenant's id is invisible -> 404
    assert client.patch(f"/conversations/{CONV_B1}", headers=H, json={"title": "x"}).status_code == 404
    # archive / unarchive
    assert client.post(f"/conversations/{CONV_A1}/archive", headers=H).json()["conversation"]["archived_at"]
    assert client.post(f"/conversations/{CONV_A1}/unarchive", headers=H).json()["conversation"]["archived_at"] is None
    assert client.post(f"/conversations/{MISSING}/archive", headers=H).status_code == 404


# --------------------------------------------------------------------------- messages
@pytest.mark.integration
def test_messages_transcript_and_404():
    store = FakeConversationStore()
    client = _client(store)
    r = client.get(f"/conversations/{CONV_A1}/messages", headers=H)
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "agent"]
    assert msgs[1]["citations"][0]["source_ref"] == "deal:1"
    # malformed / missing / cross-tenant id -> 404
    assert client.get("/conversations/not-a-uuid/messages", headers=H).status_code == 404
    assert client.get(f"/conversations/{MISSING}/messages", headers=H).status_code == 404
    assert client.get(f"/conversations/{CONV_B1}/messages", headers=H).status_code == 404


# --------------------------------------------------------------------------- /chat transcript write
class _Turn:
    def __init__(self, answer, settled=True):
        self._d = {"answer": answer, "citations": [{"claim": "c", "source_ref": "r", "snippet": "s"}],
                   "grounding_status": "grounded", "settled": settled}

    def as_dict(self):
        return dict(self._d)


class _Convo:
    def __init__(self, answer="ok", settled=True):
        self._answer, self._settled = answer, settled

    def send(self, message, **kw):
        return _Turn(self._answer, self._settled)


@pytest.mark.integration
def test_chat_persists_transcript_when_settled():
    store = FakeConversationStore()
    client = _client(store, conversation_factory=lambda t, c=None: _Convo())
    r = client.post("/chat", headers=H, json={"message": "hello", "conversation_id": CONV_A1})
    assert r.status_code == 200
    roles = [(a["role"], a["content"]) for a in store.appended]
    assert ("user", "hello") in roles
    assert ("agent", "ok") in roles
    # the agent row carried the turn's citations + grounding
    agent = [a for a in store.appended if a["role"] == "agent"][0]
    assert agent["citations"] and agent["grounding_status"] == "grounded"


@pytest.mark.integration
def test_chat_unknown_conversation_is_404():
    store = FakeConversationStore()
    client = _client(store, conversation_factory=lambda t, c=None: _Convo())
    r = client.post("/chat", headers=H, json={"message": "hi", "conversation_id": MISSING})
    assert r.status_code == 404
    assert store.appended == []  # nothing persisted for a bad thread


@pytest.mark.integration
def test_chat_without_conversation_id_never_touches_store():
    store = FakeConversationStore()
    client = _client(store, conversation_factory=lambda t, c=None: _Convo())
    r = client.post("/chat", headers=H, json={"message": "hi"})  # legacy tenant-level path
    assert r.status_code == 200
    assert store.appended == []


@pytest.mark.integration
def test_chat_async_turn_persists_user_now_agent_on_continue():
    store = FakeConversationStore()
    # First turn unsettled: only the user row lands; the agent row waits for /chat/continue.
    client = _client(store, conversation_factory=lambda t, c=None: _Convo(settled=False))
    client.post("/chat", headers=H, json={"message": "deep ask", "conversation_id": CONV_A1})
    assert [a["role"] for a in store.appended] == ["user"]
