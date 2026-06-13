"""Unit: the /onboarding routes (first-run experience) — claims-bound, RLS-discipline honored.

Mounts `mount_onboarding` on a bare FastAPI app with a fake verifier + an in-memory store fake
(mirroring the FakeDirectoryReader pattern in tests/integration/test_api_contacts.py), so the
behavior is proven with NO database:
  * 401 unauth on every route (the shared current_tenant dependency)
  * the tenant is ALWAYS the verified claim — a smuggled tenant (body/query) is ignored
  * GET /onboarding returns the honest fresh default for a brand-new tenant
  * GET/PUT round-trip: a PUT toggling one step persists, merges (never clears the others),
    and GET reflects it; dismissed flips independently
  * an unknown step id is a 422 (the flat allow-list never accretes hostile keys)
  * POST /onboarding/load-sample is idempotent (loads once; re-running reports counts, no dup),
    marks sample_loaded + the load_data step done, and surfaces the populated state
  * unconfigured data plane: GET serves the honest default; PUT + load-sample answer honest 503
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import make_current_tenant
from api.onboarding_routes import (
    STEP_IDS,
    OnboardingDeps,
    OnboardingStateStore,
    mount_onboarding,
)

H = {"Authorization": "Bearer t"}


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}


class FakeStore:
    """In-memory onboarding store keyed by tenant — honors the RLS contract (a read/write for
    tenant A can never surface tenant B's row) and records the tenant of every call so tests can
    assert the claim steered each one. Shapes mirror OnboardingStateStore's return values."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.calls: list[tuple] = []

    def _default(self, tenant_id):
        return {
            "tenant_id": str(tenant_id),
            "steps": {sid: False for sid in STEP_IDS},
            "dismissed": False,
            "sample_loaded": False,
        }

    def get(self, tenant_id):
        self.calls.append(("get", str(tenant_id)))
        return dict(self.rows.get(str(tenant_id), self._default(tenant_id)))

    def upsert(self, tenant_id, *, steps=None, dismissed=None, sample_loaded=None):
        self.calls.append(("upsert", str(tenant_id)))
        row = self.rows.get(str(tenant_id)) or self._default(tenant_id)
        merged = {sid: bool(row["steps"].get(sid, False)) for sid in STEP_IDS}
        if steps:
            for sid, done in steps.items():
                if sid in merged:
                    merged[sid] = bool(done)
        row = {
            "tenant_id": str(tenant_id),
            "steps": merged,
            "dismissed": bool(dismissed) if dismissed is not None else row["dismissed"],
            "sample_loaded": bool(sample_loaded) if sample_loaded is not None else row["sample_loaded"],
        }
        self.rows[str(tenant_id)] = row
        return dict(row)


def _client(store=None, sample_loader=None, ingest_document=None):
    app = FastAPI()
    kwargs = {}
    if store is not None:
        kwargs["store"] = store
    if sample_loader is not None:
        kwargs["sample_loader"] = sample_loader
    if ingest_document is not None:
        kwargs["ingest_document"] = ingest_document
    deps = OnboardingDeps(**kwargs)
    mount_onboarding(app, deps, make_current_tenant(FakeVerifier()))
    return TestClient(app)


# --------------------------------------------------------------------------- auth
@pytest.mark.unit
def test_unauth_is_401_on_every_route():
    c = _client(FakeStore())
    assert c.get("/onboarding").status_code == 401
    assert c.put("/onboarding", json={"dismissed": True}).status_code == 401
    assert c.post("/onboarding/load-sample").status_code == 401


@pytest.mark.unit
def test_get_fresh_default_for_new_tenant():
    c = _client(FakeStore())
    r = c.get("/onboarding", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "A"
    assert body["dismissed"] is False
    assert body["sample_loaded"] is False
    assert body["steps"] == {sid: False for sid in STEP_IDS}


@pytest.mark.unit
def test_put_get_round_trip_merges_steps():
    store = FakeStore()
    c = _client(store)
    # Toggle one step done.
    r = c.put("/onboarding", json={"steps": {"try_chat": True}}, headers=H)
    assert r.status_code == 200
    assert r.json()["steps"]["try_chat"] is True
    # A second PUT toggling a DIFFERENT step must not clear the first.
    r2 = c.put("/onboarding", json={"steps": {"invite_team": True}}, headers=H)
    assert r2.json()["steps"]["try_chat"] is True
    assert r2.json()["steps"]["invite_team"] is True
    # GET reflects the persisted, merged state.
    got = c.get("/onboarding", headers=H).json()
    assert got["steps"]["try_chat"] is True
    assert got["steps"]["invite_team"] is True
    assert got["steps"]["load_data"] is False


@pytest.mark.unit
def test_put_dismissed_flips_independently():
    store = FakeStore()
    c = _client(store)
    c.put("/onboarding", json={"steps": {"try_chat": True}}, headers=H)
    r = c.put("/onboarding", json={"dismissed": True}, headers=H)
    assert r.json()["dismissed"] is True
    assert r.json()["steps"]["try_chat"] is True  # dismissing never clears progress


@pytest.mark.unit
def test_unknown_step_is_422():
    c = _client(FakeStore())
    r = c.put("/onboarding", json={"steps": {"hack_admin": True}}, headers=H)
    assert r.status_code == 422
    assert "hack_admin" in r.json()["detail"]


@pytest.mark.unit
def test_tenant_is_always_the_claim_never_the_body():
    store = FakeStore()
    c = _client(store)
    # A smuggled tenant in the body is ignored — the typed body has no such field, and the store
    # is only ever called with the verified claim "A".
    c.put("/onboarding", json={"steps": {"try_chat": True}, "tenant_id": "EVIL"}, headers=H)
    assert all(t == "A" for _, t in store.calls)
    assert "EVIL" not in store.rows


# --------------------------------------------------------------------------- load-sample
@pytest.mark.unit
def test_load_sample_idempotent_and_marks_done():
    store = FakeStore()
    load_calls = {"n": 0}

    def fake_loader(s, tenant_id):
        # Idempotent loader fake: returns the SAME counts each call (a real wipe-then-insert
        # never duplicates), and proves the route hands it the verified-claim tenant.
        assert tenant_id == "A"
        load_calls["n"] += 1
        return {"companies": 40, "contacts": 120, "deals": 60, "documents": 449}

    c = _client(store, sample_loader=fake_loader)
    r = c.post("/onboarding/load-sample", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["loaded"] is True
    assert body["counts"]["companies"] == 40
    assert body["onboarding"]["sample_loaded"] is True
    assert body["onboarding"]["steps"]["load_data"] is True

    # Re-running is safe: counts unchanged, flags stay done, the row is not duplicated.
    r2 = c.post("/onboarding/load-sample", headers=H)
    assert r2.json()["counts"]["companies"] == 40
    assert r2.json()["onboarding"]["sample_loaded"] is True
    assert load_calls["n"] == 2  # the route called the (idempotent) loader each time
    assert len(store.rows) == 1  # exactly one tenant row, never duplicated


# --------------------------------------------------------------------------- sample pages
def _crm_loader(s, tenant_id):
    return {"companies": 1, "documents": 3}


@pytest.mark.unit
def test_load_sample_seeds_knowledge_pages_via_the_ingest_seam():
    from api.onboarding_routes import SAMPLE_PAGES

    seeded: list[tuple] = []

    def fake_ingest(tenant_id, title, content):
        seeded.append((tenant_id, title))
        return {"ref_id": f"upload:x-{len(seeded)}", "chunks": 1}

    c = _client(FakeStore(), sample_loader=_crm_loader, ingest_document=fake_ingest)
    body = c.post("/onboarding/load-sample", headers=H).json()
    assert body["loaded"] is True
    assert body["knowledge"] == {"pages_seeded": len(SAMPLE_PAGES), "reason": None}
    # Every page ran under the VERIFIED claims tenant with the curated titles, in order.
    assert seeded == [("A", t) for t, _ in SAMPLE_PAGES]

    # Re-running re-seeds idempotently (the seam upserts in place for unchanged content).
    c.post("/onboarding/load-sample", headers=H)
    assert len(seeded) == 2 * len(SAMPLE_PAGES)


@pytest.mark.unit
def test_load_sample_without_ingest_plane_reports_honestly():
    from api.onboarding_routes import REASON_PAGES_UNCONFIGURED

    c = _client(FakeStore(), sample_loader=_crm_loader)  # no ingestor wired
    body = c.post("/onboarding/load-sample", headers=H).json()
    # The CRM sample still loads; the pages half degrades honestly — never a fake success.
    assert body["loaded"] is True
    assert body["knowledge"]["pages_seeded"] == 0
    assert body["knowledge"]["reason"] == REASON_PAGES_UNCONFIGURED


@pytest.mark.unit
def test_load_sample_page_seeding_failure_never_fails_the_crm_load():
    from api.onboarding_routes import REASON_PAGES_FAILED

    calls = {"n": 0}

    def flaky_ingest(tenant_id, title, content):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("AccessDenied arn:aws:bedrock-XYZZY")
        return {"ref_id": "upload:x-1", "chunks": 1}

    c = _client(FakeStore(), sample_loader=_crm_loader, ingest_document=flaky_ingest)
    r = c.post("/onboarding/load-sample", headers=H)
    body = r.json()
    # Still a 200 with loaded: true — the CRM fixture landed before the pages half ran.
    assert r.status_code == 200
    assert body["loaded"] is True
    assert body["knowledge"]["pages_seeded"] == 1  # what actually landed, honestly
    assert body["knowledge"]["reason"] == REASON_PAGES_FAILED
    assert "XYZZY" not in r.text  # the raw error never leaks


# --------------------------------------------------------------------------- unconfigured
@pytest.mark.unit
def test_unconfigured_get_serves_default_but_writes_503():
    # No store injected (data plane unconfigured).
    c = _client(store=None)
    # GET still renders the honest fresh default (the first-run UI must work in any deploy).
    g = c.get("/onboarding", headers=H)
    assert g.status_code == 200
    assert g.json()["steps"] == {sid: False for sid in STEP_IDS}
    # PUT + load-sample answer the honest 503 (no fake persistence).
    assert c.put("/onboarding", json={"dismissed": True}, headers=H).status_code == 503
    assert c.post("/onboarding/load-sample", headers=H).status_code == 503


@pytest.mark.unit
def test_default_deps_sample_loader_is_the_real_reuse():
    """The default OnboardingDeps wires the real loader (reuse of scripts/demo/load_demo_tenant.py),
    not a stub — a regression that drops the reuse fails here."""
    from api.onboarding_routes import _load_sample_into_tenant

    assert OnboardingDeps().sample_loader is _load_sample_into_tenant


@pytest.mark.unit
def test_store_class_is_import_safe_without_db():
    """Constructing the module + referencing the store class touches no AWS/DB at import time."""
    assert OnboardingStateStore is not None
    assert STEP_IDS == ("load_data", "try_chat", "invite_team")
