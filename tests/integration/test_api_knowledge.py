"""Integration: GET /knowledge + GET /knowledge/search — the real Knowledge tab (read-only).

Proves the api half of the knowledge vertical slice (the test shapes mirror
test_api_contacts.py / test_api_workflows.py):
  * 401 unauth (the shared current_tenant dependency)
  * inventory success: per-source counts + newest timestamp (ISO) + honest totals
  * inventory empty (un-ingested tenant): zeros, never invented sources
  * unconfigured (no rag injected) -> honest 503 on BOTH endpoints, never invented rows
  * search success: ref_id + source + a bounded SNIPPET + rounded score (RLS-scoped read)
  * search DEGRADES to 200 {search_available: false, reason} when the embedder/model raises
    (the Titan/Bedrock env-key gate) — never a 500, never a leaked AWS error string
  * the free-text q is required (blank -> 422) and length-capped (> MAX_Q_LEN -> 422)
  * search limit is clamped to MAX_SEARCH_LIMIT
  * THE TRUST RULE: a smuggled ?tenant_id= neither errors nor changes the tenant read
  * the default ApiDeps mounts the routes with the honest inert stub (503, never 404)
  * READ-ONLY: only GET is mounted (POST/PUT/PATCH/DELETE -> 405)
  * IMPORT SAFETY: importing the route module — and building the whole app with default deps —
    must import neither boto3 NOR ingest (the embedder is lazy, request-path only)
"""
import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.knowledge_routes import (
    MAX_Q_LEN,
    MAX_SEARCH_LIMIT,
    REASON_SEARCH_UNAVAILABLE,
    SNIPPET_LEN,
    KnowledgeDeps,
)
from api.views import SavedViews

H = {"Authorization": "Bearer t"}


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}


class FakeRag:
    """In-memory PgRagClient stand-in. Records calls so tests can assert read-only + tenant
    steering. `inventory` seeds list_document_inventory; `hits` seeds search; `search_error`
    makes search raise (the embedder-unavailable degrade path)."""

    def __init__(self, inventory=None, hits=None, search_error: Exception | None = None):
        self._inventory = list(inventory or [])
        self._hits = list(hits or [])
        self._search_error = search_error
        self.calls: list[tuple] = []

    def list_document_inventory(self, *, tenant_id: str):
        self.calls.append(("list_document_inventory", tenant_id))
        return [dict(r) for r in self._inventory]

    def search(self, *, tenant_id: str, query: str, limit: int):
        self.calls.append(("search", tenant_id, query, limit))
        if self._search_error is not None:
            raise self._search_error
        return [dict(h) for h in self._hits]


def _inventory():
    return [
        {"source": "hubspot", "document_count": 1280,
         "last_updated": dt.datetime(2026, 6, 9, 12, 0, 0, tzinfo=dt.UTC)},
        {"source": "call", "document_count": 262,
         "last_updated": dt.datetime(2026, 6, 8, 9, 30, 0, tzinfo=dt.UTC)},
        {"source": "upload", "document_count": 17, "last_updated": None},
    ]


def _hits():
    return [
        {"ref_id": "deal-42", "source": "hubspot",
         "content": "  Westlake   Galleria chiller retrofit — Pinnacle Property Partners, "
                    "negotiation stage, $284,000.  ", "score": 0.8137},
        {"ref_id": "call-7", "source": "call",
         "content": "x" * (SNIPPET_LEN + 200), "score": 0.5},
        {"ref_id": "u-1", "source": "upload", "content": None, "score": None},
    ]


def _client(knowledge=None):
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
        knowledge=knowledge if knowledge is not None else KnowledgeDeps(),
    )
    return TestClient(create_app(deps))


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_unauth_401():
    client = _client(KnowledgeDeps(rag=FakeRag(_inventory())))
    assert client.get("/knowledge").status_code == 401
    assert client.get("/knowledge/search?q=hi").status_code == 401


# --------------------------------------------------------------------------- #
# inventory
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_inventory_success_shape():
    rag = FakeRag(_inventory())
    r = _client(KnowledgeDeps(rag=rag)).get("/knowledge", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert [s["source"] for s in body["sources"]] == ["hubspot", "call", "upload"]
    assert body["source_count"] == 3
    assert body["total_documents"] == 1280 + 262 + 17
    assert body["sources"][0]["document_count"] == 1280
    assert body["sources"][0]["last_updated"] == "2026-06-09T12:00:00+00:00"
    assert body["sources"][2]["last_updated"] is None  # tolerates a null timestamp
    # The read was tenant-steered by the verified claim (tenant "A"), nothing else.
    assert rag.calls == [("list_document_inventory", "A")]


@pytest.mark.integration
def test_inventory_empty_un_ingested_tenant():
    r = _client(KnowledgeDeps(rag=FakeRag([]))).get("/knowledge", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body == {"sources": [], "source_count": 0, "total_documents": 0}


@pytest.mark.integration
def test_inventory_unconfigured_503():
    # No rag injected (the inert default) -> honest 503, never invented sources, never a 404.
    r = _client(KnowledgeDeps()).get("/knowledge", headers=H)
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# search — success + the honest degrade
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_search_success_shape_snippet_and_score():
    rag = FakeRag(hits=_hits())
    r = _client(KnowledgeDeps(rag=rag)).get("/knowledge/search?q=negotiation deals&limit=5",
                                            headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "negotiation deals"
    assert body["search_available"] is True
    assert body["reason"] is None
    res = body["results"]
    assert [h["ref_id"] for h in res] == ["deal-42", "call-7", "u-1"]
    # Whitespace collapsed; full content never dumped — snippet bounded to SNIPPET_LEN.
    assert res[0]["snippet"].startswith("Westlake Galleria chiller retrofit")
    assert "  " not in res[0]["snippet"]
    assert len(res[1]["snippet"]) <= SNIPPET_LEN
    assert res[1]["snippet"].endswith("…")
    assert res[0]["score"] == 0.8137
    assert res[2]["score"] is None  # tolerates a null score
    assert res[2]["snippet"] == ""  # tolerates null content
    # Only name+source+snippet+score leave — no embedding, no tenant_id, no raw row.
    assert all(set(h) == {"ref_id", "source", "snippet", "score"} for h in res)
    # Tenant-steered + limit passed through; search only (no other call).
    assert rag.calls == [("search", "A", "negotiation deals", 5)]


@pytest.mark.integration
def test_search_degrades_when_embedder_unavailable():
    # The Titan/Bedrock query embedder is env-key-gated on the live task. Any embed/model failure
    # must answer 200 with search_available:false + reason — never a 500, never a leaked AWS error.
    boom = RuntimeError("Could not connect to the endpoint URL: bedrock-runtime / NoCredentials")
    r = _client(KnowledgeDeps(rag=FakeRag(search_error=boom))).get(
        "/knowledge/search?q=anything", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["search_available"] is False
    assert body["reason"] == REASON_SEARCH_UNAVAILABLE
    assert body["results"] == []
    assert body["query"] == "anything"
    # The raw AWS error text must not leak.
    assert "bedrock" not in r.text.lower()
    assert "NoCredentials" not in r.text


@pytest.mark.integration
def test_search_unconfigured_503():
    r = _client(KnowledgeDeps()).get("/knowledge/search?q=hi", headers=H)
    assert r.status_code == 503


@pytest.mark.integration
def test_search_blank_q_is_422():
    rag = FakeRag(hits=_hits())
    client = _client(KnowledgeDeps(rag=rag))
    assert client.get("/knowledge/search", headers=H).status_code == 422
    assert client.get("/knowledge/search?q=%20%20", headers=H).status_code == 422
    # A 422'd query never reached the reader.
    assert rag.calls == []


@pytest.mark.integration
def test_search_q_too_long_is_422():
    rag = FakeRag(hits=_hits())
    r = _client(KnowledgeDeps(rag=rag)).get(
        "/knowledge/search?q=" + ("a" * (MAX_Q_LEN + 1)), headers=H)
    assert r.status_code == 422
    assert rag.calls == []


@pytest.mark.integration
def test_search_limit_clamped():
    rag = FakeRag(hits=_hits())
    _client(KnowledgeDeps(rag=rag)).get("/knowledge/search?q=hi&limit=9999", headers=H)
    assert rag.calls == [("search", "A", "hi", MAX_SEARCH_LIMIT)]


# --------------------------------------------------------------------------- #
# trust rule + read-only
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_smuggled_tenant_param_ignored():
    rag = FakeRag(_inventory())
    r = _client(KnowledgeDeps(rag=rag)).get("/knowledge?tenant_id=B&tenant=B", headers=H)
    assert r.status_code == 200
    # The read is steered by the VERIFIED claim ("A"), never the smuggled param.
    assert rag.calls == [("list_document_inventory", "A")]


@pytest.mark.integration
def test_routes_are_read_only_405_on_writes():
    rag = FakeRag(_inventory(), hits=_hits())
    client = _client(KnowledgeDeps(rag=rag))
    for path in ("/knowledge", "/knowledge/search"):
        for method in ("post", "put", "patch", "delete"):
            assert getattr(client, method)(path, headers=H).status_code == 405


@pytest.mark.integration
def test_default_apideps_mounts_routes_with_honest_inert_stub():
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
    )
    client = TestClient(create_app(deps))
    # Mounted (not 404) and honest (503), never an invented inventory.
    assert client.get("/knowledge", headers=H).status_code == 503


# --------------------------------------------------------------------------- #
# IMPORT SAFETY — no boto3, no ingest at import/mount time
# --------------------------------------------------------------------------- #
REPO = Path(__file__).resolve().parents[2]

_PROBE = r"""
import sys

class _Block:
    # Simulates a runtime without boto3/ingest: importing the knowledge route — and building the
    # whole app with default deps + hitting /knowledge — must not need either (the embedder is
    # lazy, request-path only, and the inventory needs no embedder at all).
    def find_spec(self, fullname, path=None, target=None):
        top = fullname.split(".")[0]
        if top in ("boto3", "ingest"):
            raise ModuleNotFoundError(f"No module named {fullname!r} (import-safety probe)")
        return None

sys.meta_path.insert(0, _Block())
for m in [m for m in list(sys.modules) if m.split(".")[0] in ("boto3", "ingest")]:
    del sys.modules[m]

import api.knowledge_routes  # noqa: E402 — the module under test
import api.app  # noqa: E402 — mounts the route via the inert default deps

from api.app import ApiDeps, create_app  # noqa: E402
from api.views import SavedViews  # noqa: E402
from api.control.autonomy import AutonomyConfig  # noqa: E402
from api.control.greenlight import Greenlight  # noqa: E402

class _V:
    def verify(self, token):
        return {"sub": "u", "custom:tenant_id": "A", "email": "a@x.com"}

app = create_app(ApiDeps(
    verifier=_V(), greenlight=Greenlight(), saved_views=SavedViews(),
    conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
    executor=lambda a: None,
))

from fastapi.testclient import TestClient  # noqa: E402
r = TestClient(app).get("/knowledge", headers={"Authorization": "Bearer t"})
assert r.status_code == 503, r.status_code
assert "boto3" not in sys.modules, "boto3 leaked into the import graph"
assert "ingest" not in sys.modules, "ingest leaked into the import graph"
print("KNOWLEDGE-IMPORT-SAFE-OK")
"""


@pytest.mark.integration
def test_knowledge_route_imports_and_serves_without_boto3_or_ingest():
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO, capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO)},
    )
    assert proc.returncode == 0, (
        f"knowledge route needed boto3/ingest at import/mount time:\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "KNOWLEDGE-IMPORT-SAFE-OK" in proc.stdout
