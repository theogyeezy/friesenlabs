"""Unit: GET/PUT /account/modules (api/modules_routes.py) — entitlements over a fake store."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.modules_routes import ModulesDeps, mount_modules
from api.auth import make_current_tenant


class FakeVerifier:
    def verify(self, token):
        t = token.split("-")[1] if token.startswith("t-") else "A"
        return {"sub": f"sub-{t}", "custom:tenant_id": t, "email": f"{t}@x.com"}


class _Reject:
    def verify(self, token):
        raise ValueError("bad token")


class FakeStore:
    def __init__(self, rows=None, *, boom=False):
        self._rows = rows or {}   # tenant -> enabled list
        self.boom = boom
        self.calls = []

    def get_modules(self, tenant_id):
        if self.boom:
            raise RuntimeError("column does not exist (pre-migrate)")
        return self._rows.get(str(tenant_id))

    def set_modules(self, tenant_id, ids):
        self.calls.append((str(tenant_id), list(ids)))
        self._rows[str(tenant_id)] = list(ids)
        return list(ids)


H_A = {"Authorization": "Bearer t-A"}


def _client(store=None, verifier=None):
    app = FastAPI()
    mount_modules(app, ModulesDeps(store=store), make_current_tenant(verifier or FakeVerifier()))
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
def test_503_unconfigured():
    assert _client(store=None).get("/account/modules", headers=H_A).status_code == 503


@pytest.mark.unit
def test_401_unauth():
    assert _client(store=FakeStore(), verifier=_Reject()).get("/account/modules").status_code == 401


@pytest.mark.unit
def test_get_default_when_no_row():
    r = _client(store=FakeStore()).get("/account/modules", headers=H_A)
    assert r.status_code == 200
    by_id = {m["id"]: m for m in r.json()["modules"]}
    # fresh tenant (no row) -> default = full suite (opt-out model)
    assert by_id["command"]["enabled"] is True
    assert by_id["cortex"]["enabled"] is True


@pytest.mark.unit
def test_get_returns_stored_enabled():
    r = _client(store=FakeStore({"A": ["cortex", "uplift"]})).get("/account/modules", headers=H_A)
    by_id = {m["id"]: m for m in r.json()["modules"]}
    assert by_id["cortex"]["enabled"] and by_id["uplift"]["enabled"]
    assert by_id["command"]["enabled"]   # required forced on


@pytest.mark.unit
def test_put_toggles_and_forces_required():
    store = FakeStore()
    r = _client(store=store).put("/account/modules", headers=H_A, json={"enabled": ["cortex"]})
    assert r.status_code == 200
    # required 'command' is forced on even though the client didn't send it
    saved = store.calls[-1][1]
    assert "command" in saved and "cortex" in saved
    by_id = {m["id"]: m for m in r.json()["modules"]}
    assert by_id["cortex"]["enabled"] and by_id["command"]["enabled"]
    assert by_id["workflows"]["enabled"] is False


@pytest.mark.unit
def test_put_drops_unknown_ids():
    store = FakeStore()
    _client(store=store).put("/account/modules", headers=H_A, json={"enabled": ["cortex", "bogus"]})
    assert "bogus" not in store.calls[-1][1]


@pytest.mark.unit
def test_put_all_unknown_is_422():
    r = _client(store=FakeStore()).put("/account/modules", headers=H_A, json={"enabled": ["nope"]})
    assert r.status_code == 422


@pytest.mark.unit
def test_put_tenant_from_claim_not_body():
    store = FakeStore()
    _client(store=store).put("/account/modules", headers=H_A,
                             json={"enabled": ["cortex"], "tenant_id": "EVIL"})
    assert store.calls[-1][0] == "A"   # claim tenant, not body


class _BillingOK:
    def __init__(self):
        self.calls = []

    def sync(self, tenant_id, enabled):
        self.calls.append((str(tenant_id), sorted(enabled)))
        return {"status": "synced", "added": ["price_cortex"], "removed": []}


class _BillingBoom:
    def sync(self, tenant_id, enabled):
        raise RuntimeError("stripe down")


def _client_billing(store, billing):
    app = FastAPI()
    mount_modules(app, ModulesDeps(store=store, billing=billing), make_current_tenant(FakeVerifier()))
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
def test_put_runs_billing_sync_and_includes_status():
    store, billing = FakeStore(), _BillingOK()
    r = _client_billing(store, billing).put("/account/modules", headers=H_A, json={"enabled": ["cortex"]})
    assert r.status_code == 200
    assert r.json()["billing"] == {"status": "synced", "added": ["price_cortex"], "removed": []}
    # Billing was driven with the SAVED (normalized) set — required forced on.
    assert billing.calls[-1][0] == "A"
    assert "command" in billing.calls[-1][1] and "cortex" in billing.calls[-1][1]


@pytest.mark.unit
def test_put_billing_error_is_nonfatal():
    # The entitlement row still saved; the response reports the billing error, not a 500.
    store = FakeStore()
    r = _client_billing(store, _BillingBoom()).put("/account/modules", headers=H_A, json={"enabled": ["cortex"]})
    assert r.status_code == 200
    assert r.json()["billing"]["status"] == "error"
    assert "cortex" in store.calls[-1][1]  # saved despite the billing failure


@pytest.mark.unit
def test_put_without_billing_dep_has_no_billing_field():
    r = _client(store=FakeStore()).put("/account/modules", headers=H_A, json={"enabled": ["cortex"]})
    assert r.status_code == 200
    assert "billing" not in r.json()


@pytest.mark.unit
def test_get_resilient_on_store_error():
    # A pre-migrate column / transient store error -> default catalog, NOT a 500.
    r = _client(store=FakeStore(boom=True)).get("/account/modules", headers=H_A)
    assert r.status_code == 200
    by_id = {m["id"]: m for m in r.json()["modules"]}
    assert by_id["command"]["enabled"] is True
