"""Integration: the /knowledge surface — inventory, search, and the pages CRUD.

Proves the api half of the knowledge vertical slice (the test shapes mirror
test_api_contacts.py / test_api_workflows.py):
  * 401 unauth (the shared current_tenant dependency)
  * inventory success: per-source counts + newest timestamp (ISO) + honest totals
  * inventory empty (un-ingested tenant): zeros, never invented sources
  * unconfigured (no rag injected) -> honest 503 on EVERY endpoint, never invented rows
  * search success: ref_id + source + a bounded SNIPPET + rounded score (RLS-scoped read)
  * search DEGRADES to 200 {search_available: false, reason} when the embedder/model raises
    (the Titan/Bedrock env-key gate) — never a 500, never a leaked AWS error string
  * the free-text q is required (blank -> 422) and length-capped (> MAX_Q_LEN -> 422)
  * search limit is clamped to MAX_SEARCH_LIMIT
  * THE TRUST RULE: a smuggled ?tenant_id= neither errors nor changes the tenant read
  * the default ApiDeps mounts the routes with the honest inert stub (503, never 404)
  * /knowledge and /knowledge/search stay read-only (POST/PUT/PATCH/DELETE -> 405)
  * pages (GET/PUT/DELETE /knowledge/documents[/{ref}]): list newest-first with title/preview
    out of the raw head (legacy uploads list read-only), full read parses title/body exactly,
    a malformed ref is a 422 BEFORE the reader, edits land the NEW namespace before the old
    one is removed (a cleanup failure reports previous_removed: false, never a fake failure),
    legacy edits refuse with an honest 409, deletes 404 when nothing existed for the tenant
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
    REASON_CODE_EMBEDDER,
    REASON_CODE_SEARCH_ERROR,
    REASON_SEARCH_FAILED,
    REASON_SEARCH_UNAVAILABLE,
    SNIPPET_LEN,
    KnowledgeDeps,
)
from api.pg_clients import EmbedderUnavailable
from api.views import SavedViews

H = {"Authorization": "Bearer t"}


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}


class FakeRag:
    """In-memory PgRagClient stand-in. Records calls so tests can assert read-only + tenant
    steering. `inventory` seeds list_document_inventory; `hits` seeds search; `search_error`
    makes search raise (the embedder-unavailable degrade path); `docs` seeds the pages list;
    `doc_map` (ref_prefix -> doc dict) seeds get_uploaded_document; `delete_rows`
    (ref_prefix -> rowcount) seeds delete; `delete_error` makes delete raise."""

    def __init__(self, inventory=None, hits=None, search_error: Exception | None = None,
                 docs=None, doc_map=None, delete_rows=None,
                 delete_error: Exception | None = None):
        self._inventory = list(inventory or [])
        self._hits = list(hits or [])
        self._search_error = search_error
        self._docs = list(docs or [])
        self._doc_map = dict(doc_map or {})
        self._delete_rows = dict(delete_rows or {})
        self._delete_error = delete_error
        self.calls: list[tuple] = []

    def list_document_inventory(self, *, tenant_id: str):
        self.calls.append(("list_document_inventory", tenant_id))
        return [dict(r) for r in self._inventory]

    def search(self, *, tenant_id: str, query: str, limit: int):
        self.calls.append(("search", tenant_id, query, limit))
        if self._search_error is not None:
            raise self._search_error
        return [dict(h) for h in self._hits]

    def list_uploaded_documents(self, *, tenant_id: str):
        self.calls.append(("list_uploaded_documents", tenant_id))
        return [dict(d) for d in self._docs]

    def get_uploaded_document(self, *, tenant_id: str, ref_prefix: str):
        self.calls.append(("get_uploaded_document", tenant_id, ref_prefix))
        d = self._doc_map.get(ref_prefix)
        return dict(d) if d is not None else None

    def delete_uploaded_document(self, *, tenant_id: str, ref_prefix: str):
        self.calls.append(("delete_uploaded_document", tenant_id, ref_prefix))
        if self._delete_error is not None:
            raise self._delete_error
        return self._delete_rows.get(ref_prefix, 0)


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
    # The Titan/Bedrock query embedder is env-key-gated on the live task. An embed failure
    # (the TYPED EmbedderUnavailable boundary from PgRagClient._embed) must answer 200 with
    # the "warming up" story — never a 500, never a leaked AWS error.
    boom = EmbedderUnavailable("Could not connect to the endpoint URL: bedrock-runtime / NoCredentials")
    r = _client(KnowledgeDeps(rag=FakeRag(search_error=boom))).get(
        "/knowledge/search?q=anything", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["search_available"] is False
    assert body["reason"] == REASON_SEARCH_UNAVAILABLE
    assert body["reason_code"] == REASON_CODE_EMBEDDER
    assert body["results"] == []
    assert body["query"] == "anything"
    # The raw AWS error text must not leak.
    assert "bedrock" not in r.text.lower()
    assert "NoCredentials" not in r.text


@pytest.mark.integration
def test_search_transient_failure_is_not_the_warming_up_story():
    # Knowledge audit P1: a failure AFTER the embed (DB read/pool) must NOT read "search model
    # not configured" — it's transient, the UI offers a retry. Same honesty rules otherwise:
    # 200, search_available:false, no leaked detail.
    boom = RuntimeError("FATAL: connection to server at aurora-ARN-SECRET failed")
    r = _client(KnowledgeDeps(rag=FakeRag(search_error=boom))).get(
        "/knowledge/search?q=anything", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["search_available"] is False
    assert body["reason"] == REASON_SEARCH_FAILED
    assert body["reason_code"] == REASON_CODE_SEARCH_ERROR
    assert body["results"] == []
    assert "ARN-SECRET" not in r.text  # the raw DB error never leaks


@pytest.mark.integration
def test_pg_rag_client_wraps_embed_failures_typed():
    """The boundary itself: an embedder failure surfaces as EmbedderUnavailable BEFORE any
    connection checkout — the route can classify without string-sniffing, and a Bedrock
    outage never even touches the pool."""
    from api.pg_clients import PgRagClient

    conns: list = []

    def no_db():
        conns.append("conn")
        raise AssertionError("the failed embed must never reach the DB")

    def broken_embedder(q):
        raise RuntimeError("NoCredentials: bedrock-runtime")

    client = PgRagClient(conn_factory=no_db, embedder=broken_embedder)
    with pytest.raises(EmbedderUnavailable):
        client.search(tenant_id="A", query="x", limit=3)
    assert conns == []


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


# --------------------------------------------------------------------------- #
# POST /knowledge/documents — the customer document-add path (knowledge audit P0)
# --------------------------------------------------------------------------- #
class FakeIngestor:
    """Stand-in for the chunk→embed→upsert callable (ingest.upload via build_doc_ingestor)."""

    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple] = []
        self._error = error

    def __call__(self, tenant_id: str, title: str, content: str):
        self.calls.append((tenant_id, title, content))
        if self._error is not None:
            raise self._error
        return {"ref_id": "upload:pricing-policy-ab12cd34", "chunks": 2, "source": "upload"}


def test_add_document_lands_201_under_claims_tenant():
    ing = FakeIngestor()
    client = _client(KnowledgeDeps(rag=FakeRag(_inventory()), ingest_document=ing))
    r = client.post("/knowledge/documents", headers=H, json={
        "title": "Pricing Policy", "content": "Discounts cap at 15%.",
        "tenant_id": "EVIL",  # smuggled — must be ignored (THE TRUST RULE)
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ref_id"] == "upload:pricing-policy-ab12cd34"
    assert body["chunks"] == 2
    assert body["source"] == "upload"
    # The ingestor ran under the VERIFIED claims tenant, never the smuggled one.
    assert ing.calls == [("A", "Pricing Policy", "Discounts cap at 15%.")]


def test_add_document_unconfigured_503():
    client = _client(KnowledgeDeps(rag=FakeRag(_inventory())))  # no ingestor wired
    r = client.post("/knowledge/documents", headers=H,
                    json={"title": "Doc", "content": "text"})
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


def test_add_document_validation_422():
    ing = FakeIngestor()
    client = _client(KnowledgeDeps(rag=FakeRag(_inventory()), ingest_document=ing))
    for bad in (
        {"title": "  ", "content": "text"},
        {"title": "Doc", "content": "   "},
        {"content": "text"},
        {"title": "Doc"},
        {"title": "T" * 201, "content": "text"},
        {"title": "Doc", "content": "x" * 100_001},
    ):
        assert client.post("/knowledge/documents", headers=H, json=bad).status_code == 422, bad
    assert ing.calls == []  # nothing invalid ever reaches the ingest plane


def test_add_document_ingest_failure_is_503_and_never_leaks():
    ing = FakeIngestor(error=RuntimeError("AccessDenied arn:aws:secret-XYZZY"))
    client = _client(KnowledgeDeps(rag=FakeRag(_inventory()), ingest_document=ing))
    r = client.post("/knowledge/documents", headers=H,
                    json={"title": "Doc", "content": "text"})
    assert r.status_code == 503
    assert "XYZZY" not in r.text  # a write fails LOUD but the raw error never leaks


def test_add_document_requires_auth():
    client = _client(KnowledgeDeps(rag=FakeRag(_inventory()), ingest_document=FakeIngestor()))
    assert client.post("/knowledge/documents",
                       json={"title": "Doc", "content": "text"}).status_code == 401


# --------------------------------------------------------------------------- #
# Pages — GET/PUT/DELETE /knowledge/documents[/{ref}] (the editable knowledge surface)
# --------------------------------------------------------------------------- #
REF_A = "upload:pricing-policy-ab12cd34"
REF_B = "upload:onboarding-sop-99fe01aa"
REF_LEGACY = "upload:old-playbook-00aa11bb"
REF_NEW = "upload:pricing-policy-deadbeef"  # what FakeIngestor returns after an edit

import datetime as _dt  # noqa: E402 — local alias for the page fixtures below

_T1 = _dt.datetime(2026, 6, 11, 9, 0, 0, tzinfo=_dt.UTC)
_T2 = _dt.datetime(2026, 6, 12, 10, 30, 0, tzinfo=_dt.UTC)


def _docs():
    return [
        {"ref_id": REF_B, "raw_head": "Onboarding SOP\n\nDay one: badge,   laptop, intro call.",
         "chunk_count": 3, "created_at": _T2, "updated_at": _T2},
        {"ref_id": REF_LEGACY, "raw_head": None,  # pre-raw-row upload: read-only
         "chunk_count": 2, "created_at": _T1, "updated_at": _T1},
    ]


def _doc_map():
    return {
        REF_A: {"ref_id": REF_A,
                "raw_content": "Pricing Policy\n\nDiscounts cap at 15%.\n\n- list rates apply",
                "chunk_count": 2, "chunk_contents": ["chunk0", "chunk1"],
                "created_at": _T1, "updated_at": _T2},
        REF_LEGACY: {"ref_id": REF_LEGACY, "raw_content": None,
                     "chunk_count": 2, "chunk_contents": ["legacy chunk 0", "legacy chunk 1"],
                     "created_at": _T1, "updated_at": _T1},
    }


class FakeEditIngestor(FakeIngestor):
    """An ingestor whose result ref is configurable (same-namespace vs new-namespace edits)."""

    def __init__(self, ref_id: str = REF_NEW, error: Exception | None = None):
        super().__init__(error=error)
        self._ref_id = ref_id

    def __call__(self, tenant_id: str, title: str, content: str):
        self.calls.append((tenant_id, title, content))
        if self._error is not None:
            raise self._error
        return {"ref_id": self._ref_id, "chunks": 2, "source": "upload", "title": title}


def test_list_documents_shapes_titles_and_previews():
    rag = FakeRag(docs=_docs())
    r = _client(KnowledgeDeps(rag=rag)).get("/knowledge/documents", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    fresh, legacy = body["documents"]
    # The raw head parses into an exact title + a collapsed, bounded preview.
    assert fresh == {"ref_id": REF_B, "title": "Onboarding SOP",
                     "preview": "Day one: badge, laptop, intro call.",
                     "chunks": 3, "editable": True,
                     "created_at": "2026-06-12T10:30:00+00:00",
                     "updated_at": "2026-06-12T10:30:00+00:00"}
    # A legacy upload de-slugs its ref for a title and lists read-only — never invented text.
    assert legacy["ref_id"] == REF_LEGACY
    assert legacy["title"] == "Old playbook"
    assert legacy["preview"] == ""
    assert legacy["editable"] is False
    assert rag.calls == [("list_uploaded_documents", "A")]


def test_list_documents_empty_and_unconfigured_and_auth():
    assert _client(KnowledgeDeps(rag=FakeRag())).get(
        "/knowledge/documents", headers=H).json() == {"documents": [], "total": 0}
    assert _client(KnowledgeDeps()).get(
        "/knowledge/documents", headers=H).status_code == 503
    assert _client(KnowledgeDeps(rag=FakeRag())).get(
        "/knowledge/documents").status_code == 401


def test_get_document_returns_exact_original():
    rag = FakeRag(doc_map=_doc_map())
    r = _client(KnowledgeDeps(rag=rag)).get(f"/knowledge/documents/{REF_A}", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Pricing Policy"
    # The body comes back EXACTLY as stored — newlines intact, nothing collapsed.
    assert body["content"] == "Discounts cap at 15%.\n\n- list rates apply"
    assert body["editable"] is True
    assert body["sections"] is None
    assert body["chunks"] == 2
    assert rag.calls == [("get_uploaded_document", "A", REF_A)]


def test_get_document_legacy_degrades_to_chunk_sections():
    rag = FakeRag(doc_map=_doc_map())
    r = _client(KnowledgeDeps(rag=rag)).get(f"/knowledge/documents/{REF_LEGACY}", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["editable"] is False
    assert body["content"] is None
    assert body["sections"] == ["legacy chunk 0", "legacy chunk 1"]
    assert body["title"] == "Old playbook"


def test_get_document_404_and_bad_ref_422():
    rag = FakeRag(doc_map=_doc_map())
    client = _client(KnowledgeDeps(rag=rag))
    assert client.get(f"/knowledge/documents/{REF_NEW}", headers=H).status_code == 404
    # Malformed refs are refused BEFORE the reader: uppercase, LIKE wildcards, a smuggled
    # chunk-seq suffix, whitespace. (A well-formed ref under another source — e.g.
    # 'hubspot:deal-42' — passes the shape check and gets an honest 404 from the
    # source='upload'-scoped reader instead.)
    for bad in ("upload:UPPER-ab12cd34", "upload:a%25b-ab12cd34",
                f"{REF_A}%23raw", "has%20space", "-leading-dash"):
        assert client.get(f"/knowledge/documents/{bad}", headers=H).status_code == 422, bad
    # Only the valid (404) lookup reached the reader.
    assert rag.calls == [("get_uploaded_document", "A", REF_NEW)]


def test_seeded_demo_refs_are_valid_pages():
    """The demo corpus (scripts/demo/seed_knowledge.py) lives under source='upload' with
    `demo:kb:<slug>` refs and NO raw row — it must list with a readable de-slugged title,
    open read-only, and be deletable (not 422 on its ref shape)."""
    demo_ref = "demo:kb:pricing-discount-authority"
    rag = FakeRag(
        docs=[{"ref_id": demo_ref, "raw_head": None, "chunk_count": 1,
               "created_at": _T1, "updated_at": _T1}],
        doc_map={demo_ref: {"ref_id": demo_ref, "raw_content": None, "chunk_count": 1,
                            "chunk_contents": ["Discount authority: 15% cap."],
                            "created_at": _T1, "updated_at": _T1}},
        delete_rows={demo_ref: 1},
    )
    client = _client(KnowledgeDeps(rag=rag))
    listed = client.get("/knowledge/documents", headers=H).json()["documents"][0]
    assert listed["title"] == "Pricing discount authority"
    assert listed["editable"] is False
    got = client.get(f"/knowledge/documents/{demo_ref}", headers=H)
    assert got.status_code == 200
    assert got.json()["sections"] == ["Discount authority: 15% cap."]
    assert client.delete(f"/knowledge/documents/{demo_ref}", headers=H).status_code == 200


def test_update_document_new_namespace_lands_then_cleans_old():
    rag = FakeRag(doc_map=_doc_map(), delete_rows={REF_A: 3})
    ing = FakeEditIngestor(ref_id=REF_NEW)
    client = _client(KnowledgeDeps(rag=rag, ingest_document=ing))
    r = client.put(f"/knowledge/documents/{REF_A}", headers=H, json={
        "title": "Pricing Policy", "content": "Discounts cap at 20% now.",
        "tenant_id": "EVIL",  # smuggled — ignored (THE TRUST RULE)
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ref_id"] == REF_NEW
    assert body["replaced_ref_id"] == REF_A
    assert body["previous_removed"] is True
    # Ingest ran under the claims tenant; the OLD namespace was deleted AFTER the new landed.
    assert ing.calls == [("A", "Pricing Policy", "Discounts cap at 20% now.")]
    assert rag.calls == [("get_uploaded_document", "A", REF_A),
                         ("delete_uploaded_document", "A", REF_A)]


def test_update_document_same_namespace_skips_delete():
    rag = FakeRag(doc_map=_doc_map())
    ing = FakeEditIngestor(ref_id=REF_A)  # unchanged content -> same namespace, in-place upsert
    client = _client(KnowledgeDeps(rag=rag, ingest_document=ing))
    r = client.put(f"/knowledge/documents/{REF_A}", headers=H,
                   json={"title": "Pricing Policy", "content": "Discounts cap at 15%."})
    assert r.status_code == 200
    assert r.json()["ref_id"] == REF_A
    # No delete call — deleting the "old" namespace would have deleted the live document.
    assert [c[0] for c in rag.calls] == ["get_uploaded_document"]


def test_update_document_cleanup_failure_reports_honestly():
    # The NEW version landed; the old-namespace delete failing must NOT report a failed edit.
    rag = FakeRag(doc_map=_doc_map(), delete_error=RuntimeError("pg down ARN-SECRET"))
    ing = FakeEditIngestor(ref_id=REF_NEW)
    client = _client(KnowledgeDeps(rag=rag, ingest_document=ing))
    r = client.put(f"/knowledge/documents/{REF_A}", headers=H,
                   json={"title": "Pricing Policy", "content": "new text"})
    assert r.status_code == 200
    assert r.json()["previous_removed"] is False
    assert "ARN-SECRET" not in r.text  # the raw error never leaks


def test_update_document_404_409_422_503():
    rag = FakeRag(doc_map=_doc_map())
    ing = FakeEditIngestor()
    client = _client(KnowledgeDeps(rag=rag, ingest_document=ing))
    ok = {"title": "Doc", "content": "text"}
    # Missing doc -> 404; legacy (no raw original) -> honest 409; bad ref -> 422.
    assert client.put(f"/knowledge/documents/{REF_NEW}", headers=H, json=ok).status_code == 404
    assert client.put(f"/knowledge/documents/{REF_LEGACY}", headers=H, json=ok).status_code == 409
    assert client.put("/knowledge/documents/BAD%20REF", headers=H, json=ok).status_code == 422
    # Validation mirrors POST; nothing invalid reaches the ingest plane.
    assert client.put(f"/knowledge/documents/{REF_A}", headers=H,
                      json={"title": " ", "content": "x"}).status_code == 422
    assert ing.calls == []
    # No ingestor wired -> honest 503 (the reader alone can't edit).
    no_ing = _client(KnowledgeDeps(rag=FakeRag(doc_map=_doc_map())))
    assert no_ing.put(f"/knowledge/documents/{REF_A}", headers=H, json=ok).status_code == 503


def test_delete_document_removes_namespace():
    rag = FakeRag(delete_rows={REF_A: 3})
    r = _client(KnowledgeDeps(rag=rag)).delete(f"/knowledge/documents/{REF_A}", headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"ref_id": REF_A, "deleted": True, "rows_removed": 3}
    assert rag.calls == [("delete_uploaded_document", "A", REF_A)]


def test_delete_document_404_when_nothing_existed():
    # RLS yields zero rows for another tenant's ref — the same honest 404 as a typo.
    rag = FakeRag(delete_rows={})
    r = _client(KnowledgeDeps(rag=rag)).delete(f"/knowledge/documents/{REF_A}", headers=H)
    assert r.status_code == 404


def test_delete_document_bad_ref_422_and_auth_and_unconfigured():
    rag = FakeRag(delete_rows={REF_A: 3})
    client = _client(KnowledgeDeps(rag=rag))
    assert client.delete(f"/knowledge/documents/{REF_A}%23raw", headers=H).status_code == 422
    assert rag.calls == []  # never reached the reader
    assert client.delete(f"/knowledge/documents/{REF_A}").status_code == 401
    assert _client(KnowledgeDeps()).delete(
        f"/knowledge/documents/{REF_A}", headers=H).status_code == 503
