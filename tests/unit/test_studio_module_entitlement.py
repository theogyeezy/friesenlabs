"""Unit: the server-side module-entitlement guard on /studio/* (api/routes_studio.py).

The Studio surface belongs to the "agents" module (shared.modules: that module gates the "studio"
route). A tenant that has turned the module OFF must get an honest 403 from the SERVER — not merely
a hidden nav item — because the web gate is advisory, not a security boundary. The guard mirrors
shared.modules.enabled_routes (the same normalization the catalog/web gate use) so the server can
never drift open from the catalog.

Degrade-OPEN posture (mirrors modules_routes' resilient GET + the inert-default contract):
  * no modules_store wired  -> can't know the entitlement -> ALLOW (never a false 403)
  * a store read failure     -> ALLOW + (logged) — a transient store error never locks a tenant out
A tenant whose stored set genuinely omits the module -> 403.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import TenantClaims
from api.routes_studio import StudioDeps, mount_studio


def _current_tenant() -> TenantClaims:
    # A fixed verified-claims stand-in (the real dependency validates the Cognito JWT).
    return TenantClaims(tenant_id="T1", sub="u1", email="a@x.com")


class _FakePlaybookStore:
    """Minimal PlaybookStore so store-backed routes don't 503 before the guard is exercised."""

    def list(self, tenant_id):
        return []


class _FakeModulesStore:
    """PgSettingsStore-shaped reader: get_modules(tenant_id) -> the stored enabled-module ids."""

    def __init__(self, ids, *, raises=False):
        self._ids = ids
        self._raises = raises

    def get_modules(self, tenant_id):
        if self._raises:
            raise RuntimeError("enabled_modules column predates the live migrate")
        return self._ids


def _client(modules_store=None) -> TestClient:
    app = FastAPI()
    deps = StudioDeps(store=_FakePlaybookStore(), modules_store=modules_store)
    mount_studio(app, deps, _current_tenant)
    return TestClient(app, raise_server_exceptions=True)


@pytest.mark.unit
def test_module_disabled_returns_403_on_templates_and_playbooks():
    # The tenant has a row that does NOT include "agents" -> /studio/* is forbidden, honestly.
    client = _client(_FakeModulesStore(["command", "uplift"]))
    for path in ("/studio/templates", "/studio/playbooks"):
        resp = client.get(path)
        assert resp.status_code == 403, (path, resp.status_code)
        assert "Agents module" in resp.json()["detail"]


@pytest.mark.unit
def test_module_enabled_allows_access():
    # "agents" enabled -> the guard passes and the route runs normally (200).
    client = _client(_FakeModulesStore(["command", "agents"]))
    resp = client.get("/studio/templates")
    assert resp.status_code == 200
    assert "templates" in resp.json()


@pytest.mark.unit
def test_no_store_wired_degrades_open():
    # No modules_store on this task -> enforcement is inert (the inert-default contract); the route
    # is reachable. Real enforcement turns on only when api/asgi.py wires the PgSettingsStore.
    client = _client(modules_store=None)
    assert client.get("/studio/templates").status_code == 200


@pytest.mark.unit
def test_no_row_yet_uses_provisioning_default_which_includes_agents():
    # A tenant that has never toggled (get_modules -> None) gets the FULL default suite, which
    # includes "agents" -> allowed. No new tenant is locked out before they tailor their suite.
    client = _client(_FakeModulesStore(None))
    assert client.get("/studio/templates").status_code == 200


@pytest.mark.unit
def test_store_read_failure_degrades_open():
    # A store read error (e.g. pre-migrate column) must NOT 403 a paying tenant — degrade open,
    # exactly like modules_routes' resilient GET.
    client = _client(_FakeModulesStore(None, raises=True))
    assert client.get("/studio/templates").status_code == 200
