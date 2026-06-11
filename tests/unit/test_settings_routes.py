"""Unit: GET/PUT /account/settings — persisted workspace settings (api/settings_routes.py).

Mounts ``mount_settings`` on a bare FastAPI app with a fake verifier and an in-memory store that
faithfully mimics the PgSettingsStore partial-upsert contract (only provided fields change). Zero DB,
zero AWS. Covers:

  * 503 unconfigured: store=None -> 503 on both GET and PUT (honest, never 500)
  * 401 unauth: missing / invalid bearer -> 401
  * GET returns the stored {workspace_name, notification_prefs} shape (and an empty default for a
    tenant that has never saved)
  * PUT persists and returns the saved row
  * tenant-from-claim-not-body: a tenant_id smuggled in the body is ignored; the saved row lands on
    the verified-claim tenant, and a second tenant never sees it
  * PUT validation: empty workspace_name -> 422, bad notification_prefs (nested / non-bool-or-str /
    oversized) -> 422
  * partial PUT (only workspace_name) leaves notification_prefs untouched (and vice versa)
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import make_current_tenant
from api.settings_routes import SettingsDeps, mount_settings


# --------------------------------------------------------------------------- fakes

class FakeVerifier:
    """Accepts any Bearer; maps 't-A' -> tenant 'A', 't-B' -> tenant 'B' (else 'A')."""

    def verify(self, token: str) -> dict:
        tenant = token.split("-")[1] if token.startswith("t-") else "A"
        return {"sub": f"sub-{tenant}", "custom:tenant_id": tenant, "email": f"{tenant}@x.com"}


class FakeSettingsStore:
    """In-memory store mirroring PgSettingsStore's partial-upsert contract.

    Each tenant maps to {"workspace_name": str|None, "notification_prefs": dict}. `upsert` updates
    ONLY the fields that were provided (None == leave untouched), exactly like the SQL COALESCE arm.
    """

    def __init__(self, seed: dict | None = None):
        # seed: dict[tenant_id -> {"workspace_name":..., "notification_prefs":...}]
        self._rows: dict[str, dict] = {}
        for tid, row in (seed or {}).items():
            self._rows[str(tid)] = {
                "workspace_name": row.get("workspace_name"),
                "notification_prefs": dict(row.get("notification_prefs") or {}),
            }

    def get(self, tenant_id) -> dict | None:
        row = self._rows.get(str(tenant_id))
        return None if row is None else dict(row)

    def upsert(self, tenant_id, *, workspace_name=None, notification_prefs=None) -> dict:
        if workspace_name is None and notification_prefs is None:
            raise ValueError("upsert requires at least one field")
        row = self._rows.setdefault(
            str(tenant_id), {"workspace_name": None, "notification_prefs": {}})
        if workspace_name is not None:
            row["workspace_name"] = workspace_name
        if notification_prefs is not None:
            row["notification_prefs"] = dict(notification_prefs)
        return dict(row)


# --------------------------------------------------------------------------- helpers

H_A = {"Authorization": "Bearer t-A"}
H_B = {"Authorization": "Bearer t-B"}


def _client(store=None, *, verifier=None) -> TestClient:
    app = FastAPI()
    deps = SettingsDeps(store=store)
    mount_settings(app, deps, make_current_tenant(verifier or FakeVerifier()))
    return TestClient(app)


# --------------------------------------------------------------------------- 503 unconfigured

@pytest.mark.unit
def test_get_503_when_store_none():
    c = _client(store=None)
    r = c.get("/account/settings", headers=H_A)
    assert r.status_code == 503
    assert "configured" in r.json()["detail"].lower()


@pytest.mark.unit
def test_put_503_when_store_none():
    c = _client(store=None)
    r = c.put("/account/settings", json={"workspace_name": "Acme"}, headers=H_A)
    assert r.status_code == 503


# --------------------------------------------------------------------------- auth

@pytest.mark.unit
def test_get_requires_bearer():
    c = _client(store=FakeSettingsStore())
    r = c.get("/account/settings")
    assert r.status_code == 401


@pytest.mark.unit
def test_put_invalid_token_401():
    class _RejectAll:
        def verify(self, token):
            raise ValueError("invalid")

    c = _client(store=FakeSettingsStore(), verifier=_RejectAll())
    r = c.put("/account/settings", json={"workspace_name": "Acme"},
              headers={"Authorization": "Bearer bad"})
    assert r.status_code == 401


# --------------------------------------------------------------------------- GET shape

@pytest.mark.unit
def test_get_returns_stored_shape():
    store = FakeSettingsStore(seed={
        "A": {"workspace_name": "Acme HQ", "notification_prefs": {"email_digest": True,
                                                                  "tone": "warm"}},
    })
    c = _client(store=store)
    r = c.get("/account/settings", headers=H_A)
    assert r.status_code == 200
    body = r.json()
    assert body == {"workspace_name": "Acme HQ",
                    "notification_prefs": {"email_digest": True, "tone": "warm"}}


@pytest.mark.unit
def test_get_empty_default_when_never_saved():
    """A tenant with no row gets the empty/default shape, never a 404."""
    c = _client(store=FakeSettingsStore())
    r = c.get("/account/settings", headers=H_A)
    assert r.status_code == 200
    assert r.json() == {"workspace_name": None, "notification_prefs": {}}


# --------------------------------------------------------------------------- PUT persists

@pytest.mark.unit
def test_put_persists_and_returns_saved_row():
    store = FakeSettingsStore()
    c = _client(store=store)
    r = c.put("/account/settings",
              json={"workspace_name": "Acme HQ",
                    "notification_prefs": {"email_digest": False, "sms": True}},
              headers=H_A)
    assert r.status_code == 200
    body = r.json()
    assert body == {"workspace_name": "Acme HQ",
                    "notification_prefs": {"email_digest": False, "sms": True}}
    # ... and it is actually persisted (a follow-up GET sees it).
    g = c.get("/account/settings", headers=H_A)
    assert g.json() == body


@pytest.mark.unit
def test_put_trims_workspace_name():
    store = FakeSettingsStore()
    c = _client(store=store)
    r = c.put("/account/settings", json={"workspace_name": "  Acme HQ  "}, headers=H_A)
    assert r.status_code == 200
    assert r.json()["workspace_name"] == "Acme HQ"


# --------------------------------------------------------------------------- tenant from claim

@pytest.mark.unit
def test_tenant_from_claim_not_body():
    """A tenant_id in the request body is ignored — the row lands on the verified-claim tenant."""
    store = FakeSettingsStore()
    c = _client(store=store)
    r = c.put("/account/settings",
              json={"tenant_id": "B", "workspace_name": "Sneaky"},
              headers=H_A)
    assert r.status_code == 200
    # The save landed on tenant A (the claim), not B (the body).
    assert store.get("A") == {"workspace_name": "Sneaky", "notification_prefs": {}}
    assert store.get("B") is None
    # And tenant B's GET never sees it.
    rb = c.get("/account/settings", headers=H_B)
    assert rb.json() == {"workspace_name": None, "notification_prefs": {}}


# --------------------------------------------------------------------------- validation

@pytest.mark.unit
def test_put_empty_workspace_name_422():
    c = _client(store=FakeSettingsStore())
    for bad in ("", "   "):
        r = c.put("/account/settings", json={"workspace_name": bad}, headers=H_A)
        assert r.status_code == 422, f"expected 422 for {bad!r}"


@pytest.mark.unit
def test_put_workspace_name_too_long_422():
    c = _client(store=FakeSettingsStore())
    r = c.put("/account/settings", json={"workspace_name": "x" * 5000}, headers=H_A)
    assert r.status_code == 422


@pytest.mark.unit
def test_put_nested_prefs_422():
    """Nested dict/list values are rejected (flat map only)."""
    c = _client(store=FakeSettingsStore())
    r = c.put("/account/settings",
              json={"notification_prefs": {"channels": {"email": True}}}, headers=H_A)
    assert r.status_code == 422


@pytest.mark.unit
def test_put_non_bool_or_str_pref_value_422():
    """A numeric (non bool/str) pref value is rejected."""
    c = _client(store=FakeSettingsStore())
    r = c.put("/account/settings",
              json={"notification_prefs": {"retries": 5}}, headers=H_A)
    assert r.status_code == 422


@pytest.mark.unit
def test_put_prefs_not_object_422():
    c = _client(store=FakeSettingsStore())
    r = c.put("/account/settings",
              json={"notification_prefs": ["email", "sms"]}, headers=H_A)
    assert r.status_code == 422


@pytest.mark.unit
def test_put_empty_body_422():
    """Neither field provided -> 422 (nothing to update)."""
    c = _client(store=FakeSettingsStore())
    r = c.put("/account/settings", json={}, headers=H_A)
    assert r.status_code == 422


# --------------------------------------------------------------------------- partial PUT

@pytest.mark.unit
def test_partial_put_workspace_name_leaves_prefs_untouched():
    """A PUT with only workspace_name must NOT clobber the stored notification_prefs."""
    store = FakeSettingsStore(seed={
        "A": {"workspace_name": "Old", "notification_prefs": {"email_digest": True}},
    })
    c = _client(store=store)
    r = c.put("/account/settings", json={"workspace_name": "New"}, headers=H_A)
    assert r.status_code == 200
    assert r.json() == {"workspace_name": "New",
                        "notification_prefs": {"email_digest": True}}


@pytest.mark.unit
def test_partial_put_prefs_leaves_workspace_name_untouched():
    """A PUT with only notification_prefs must NOT clobber the stored workspace_name."""
    store = FakeSettingsStore(seed={
        "A": {"workspace_name": "Keep Me", "notification_prefs": {"email_digest": True}},
    })
    c = _client(store=store)
    r = c.put("/account/settings",
              json={"notification_prefs": {"sms": True}}, headers=H_A)
    assert r.status_code == 200
    assert r.json() == {"workspace_name": "Keep Me",
                        "notification_prefs": {"sms": True}}
