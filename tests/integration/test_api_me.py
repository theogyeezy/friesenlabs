"""Integration: GET /me + /api/me — SPA identity bootstrap from the VERIFIED claims (TODO FE/P2).

Identity comes ONLY from the verified JWT claims (THE TRUST RULE); unauth -> 401 via the standard
current_tenant dependency. Registered at both paths because the deployed Amplify rewrite strips
the /api prefix (browser /api/me -> app /me) while direct callers hit /api/me.
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.views import SavedViews


class _FakeVerifier:
    def verify(self, token):
        if token != "good":
            raise ValueError("bad token")
        return {"sub": "user-A", "custom:tenant_id": "tenant-A", "email": "a@x.com"}


def _client():
    deps = ApiDeps(
        verifier=_FakeVerifier(),
        greenlight=Greenlight(),
        saved_views=SavedViews(),
        conversation_factory=lambda tenant_id: None,
        autonomy_config=AutonomyConfig(),
        executor=lambda action: {"status": "noop"},
    )
    return TestClient(create_app(deps))


@pytest.mark.integration
@pytest.mark.parametrize("path", ["/me", "/api/me"])
def test_me_returns_identity_from_verified_claims(path):
    r = _client().get(path, headers={"Authorization": "Bearer good"})
    assert r.status_code == 200
    assert r.json() == {"email": "a@x.com", "tenant_id": "tenant-A", "name": None}


@pytest.mark.integration
@pytest.mark.parametrize("path", ["/me", "/api/me"])
def test_me_401_without_token(path):
    assert _client().get(path).status_code == 401


@pytest.mark.integration
@pytest.mark.parametrize("path", ["/me", "/api/me"])
def test_me_401_with_invalid_token(path):
    r = _client().get(path, headers={"Authorization": "Bearer forged"})
    assert r.status_code == 401
