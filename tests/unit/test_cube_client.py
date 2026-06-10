"""Unit: the Cube client mints a per-request HS256 JWT from the verified-claim tenant (THE TRUST
RULE's Cube leg) — exact tenant, real expiry, forged secrets rejected, clean unconfigured
degradations, and the query_cube/build_view constructor injection seams."""
import json

import pytest

from agents.tools.base import ToolContext
from agents.tools.build_view import BuildView
from agents.tools.cube_client import (
    CubeClient,
    CubeTokenError,
    _b64url_decode,
    cube_client_from_env,
    decode_verified,
    mint_cube_jwt,
)
from agents.tools.readonly import QueryCube
from shared.config import ENV_CUBE_ENDPOINT, ENV_CUBEJS_API_SECRET_VALUE

SECRET = "test-cube-secret"
TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"
T0 = 1_750_000_000  # fixed clock for deterministic iat/exp


def _payload_of(token: str) -> dict:
    """Decode the payload WITHOUT verification (test introspection only)."""
    return json.loads(_b64url_decode(token.split(".")[1]))


# ---------------------------------------------------------------------------- minting + verifying


@pytest.mark.unit
def test_jwt_carries_exactly_the_caller_tenant_and_nothing_else():
    token = mint_cube_jwt(SECRET, TENANT, now=lambda: T0)
    payload = _payload_of(token)
    assert payload["tenant_id"] == TENANT
    # Exactly the tenant + timestamps — no other claims exist to forge or leak.
    assert sorted(payload) == ["exp", "iat", "tenant_id"]


@pytest.mark.unit
def test_jwt_verifies_against_the_secret_with_expiry_present():
    token = mint_cube_jwt(SECRET, TENANT, ttl_s=60, now=lambda: T0)
    payload = decode_verified(token, SECRET, now=lambda: T0 + 1)
    assert payload["tenant_id"] == TENANT
    assert payload["iat"] == T0
    assert payload["exp"] == T0 + 60  # short expiry, present and exact


@pytest.mark.unit
def test_forged_secret_is_rejected():
    token = mint_cube_jwt("forged-secret", TENANT, now=lambda: T0)
    with pytest.raises(CubeTokenError, match="bad signature"):
        decode_verified(token, SECRET, now=lambda: T0 + 1)


@pytest.mark.unit
def test_expired_token_is_rejected():
    token = mint_cube_jwt(SECRET, TENANT, ttl_s=60, now=lambda: T0)
    with pytest.raises(CubeTokenError, match="expired"):
        decode_verified(token, SECRET, now=lambda: T0 + 61)


@pytest.mark.unit
def test_tampered_payload_is_rejected():
    token = mint_cube_jwt(SECRET, TENANT, now=lambda: T0)
    head, _, sig = token.split(".")
    forged_body = mint_cube_jwt(SECRET, OTHER_TENANT, now=lambda: T0).split(".")[1]
    with pytest.raises(CubeTokenError, match="bad signature"):
        decode_verified(f"{head}.{forged_body}.{sig}", SECRET, now=lambda: T0 + 1)


@pytest.mark.unit
def test_unsigned_and_malformed_tokens_are_rejected():
    token = mint_cube_jwt(SECRET, TENANT, now=lambda: T0)
    head, body, _ = token.split(".")
    for bad in (f"{head}.{body}", f"{head}.{body}.", "junk", ""):
        with pytest.raises(CubeTokenError):
            decode_verified(bad, SECRET, now=lambda: T0 + 1)


@pytest.mark.unit
def test_trust_rule_guard_rejects_junk_tenants_before_signing():
    for forged in (None, "", "   ", 42, ["t"], {"t": 1}, "x' OR '1'='1", "has space"):
        with pytest.raises(CubeTokenError, match="verified claim"):
            mint_cube_jwt(SECRET, forged, now=lambda: T0)


@pytest.mark.unit
def test_mint_without_a_secret_raises():
    with pytest.raises(CubeTokenError, match="no Cube signing secret"):
        mint_cube_jwt("", TENANT)
    with pytest.raises(CubeTokenError, match="no Cube signing secret"):
        CubeClient(endpoint="http://cube.local:4000").mint_jwt(TENANT)


# ---------------------------------------------------------------------------- unconfigured client


@pytest.mark.unit
def test_unconfigured_client_degrades_without_network():
    for client in (CubeClient(), CubeClient(endpoint="http://cube.local:4000"), CubeClient(secret=SECRET)):
        assert client.configured is False
        out = client.load(tenant_id=TENANT, query={"measures": ["Deals.count"]})
        assert out["status"] == "unconfigured"
        assert out["rows"] == []
        assert client.members(tenant_id=TENANT) == []


@pytest.mark.unit
def test_even_unconfigured_paths_enforce_the_tenant_guard():
    client = CubeClient()
    with pytest.raises(CubeTokenError):
        client.load(tenant_id="", query={})
    with pytest.raises(CubeTokenError):
        client.members(tenant_id=None)


# ---------------------------------------------------------------------------- HTTP request shape


class FakeTransport:
    """Capture the request; serve canned (status, body) responses in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, body, headers, timeout_s):
        self.calls.append({"url": url, "body": body, "headers": headers, "timeout_s": timeout_s})
        return self.responses.pop(0)


def _client(transport, **kw):
    return CubeClient(
        endpoint="http://cube.local:4000", secret=SECRET, transport=transport,
        now=lambda: T0, sleep=lambda s: None, **kw,
    )


@pytest.mark.unit
def test_load_sends_a_signed_per_request_tenant_jwt():
    transport = FakeTransport([(200, json.dumps({"data": [{"Deals.count": 7}]}).encode())])
    client = _client(transport)
    out = client.load(tenant_id=TENANT, query={"measures": ["Deals.count"]})

    assert out == {"status": "ok", "rows": [{"Deals.count": 7}]}
    call = transport.calls[0]
    assert call["url"] == "http://cube.local:4000/cubejs-api/v1/load"
    assert json.loads(call["body"]) == {"query": {"measures": ["Deals.count"]}}
    # The Authorization header is a real HS256 JWT carrying EXACTLY the caller's tenant.
    payload = decode_verified(call["headers"]["Authorization"], SECRET, now=lambda: T0 + 1)
    assert payload["tenant_id"] == TENANT


@pytest.mark.unit
def test_each_request_mints_for_its_own_caller_tenant():
    transport = FakeTransport([(200, b'{"data": []}'), (200, b'{"data": []}')])
    client = _client(transport)
    client.load(tenant_id=TENANT, query={})
    client.load(tenant_id=OTHER_TENANT, query={})
    tenants = [
        decode_verified(c["headers"]["Authorization"], SECRET, now=lambda: T0 + 1)["tenant_id"]
        for c in transport.calls
    ]
    assert tenants == [TENANT, OTHER_TENANT]  # no shared/sticky tenant state on the client


@pytest.mark.unit
def test_load_retries_continue_wait_then_succeeds():
    transport = FakeTransport([
        (200, b'{"error": "Continue wait"}'),
        (200, json.dumps({"data": [{"Deals.count": 1}]}).encode()),
    ])
    out = _client(transport).load(tenant_id=TENANT, query={})
    assert out["status"] == "ok"
    assert len(transport.calls) == 2


@pytest.mark.unit
def test_load_surfaces_http_errors_as_an_error_result():
    out = _client(FakeTransport([(403, b'{"error": "Invalid token"}')])).load(tenant_id=TENANT, query={})
    assert out == {"status": "error", "rows": [], "error": "Invalid token"}


@pytest.mark.unit
def test_members_lists_measures_and_dimensions_from_meta():
    meta = {"cubes": [{
        "name": "Deals",
        "measures": [{"name": "Deals.count"}, {"name": "Deals.pipeline_value"}],
        "dimensions": [{"name": "Deals.stage"}],
    }]}
    transport = FakeTransport([(200, json.dumps(meta).encode())])
    client = _client(transport)
    assert client.members(tenant_id=TENANT) == ["Deals.count", "Deals.pipeline_value", "Deals.stage"]
    call = transport.calls[0]
    assert call["url"] == "http://cube.local:4000/cubejs-api/v1/meta"
    assert call["body"] is None  # GET
    assert decode_verified(call["headers"]["Authorization"], SECRET, now=lambda: T0 + 1)["tenant_id"] == TENANT


@pytest.mark.unit
def test_members_degrades_to_empty_on_error():
    assert _client(FakeTransport([(500, b"boom")])).members(tenant_id=TENANT) == []


# ---------------------------------------------------------------------------- env factory


@pytest.mark.unit
def test_cube_client_from_env_unset_returns_none(monkeypatch):
    monkeypatch.delenv(ENV_CUBE_ENDPOINT, raising=False)
    monkeypatch.delenv(ENV_CUBEJS_API_SECRET_VALUE, raising=False)
    assert cube_client_from_env() is None  # unconfigured boots stay byte-identical


@pytest.mark.unit
def test_cube_client_from_env_fully_configured(monkeypatch):
    monkeypatch.setenv(ENV_CUBE_ENDPOINT, "http://cube.local:4000")
    monkeypatch.setenv(ENV_CUBEJS_API_SECRET_VALUE, SECRET)
    client = cube_client_from_env()
    assert isinstance(client, CubeClient)
    assert client.configured is True
    # The minted token verifies against the env-injected secret value.
    assert decode_verified(client.mint_jwt(TENANT), SECRET)["tenant_id"] == TENANT


@pytest.mark.unit
def test_cube_client_from_env_partial_config_degrades_visibly(monkeypatch):
    # Endpoint without the NEW deliberate secret name (the env the live tasks may already carry)
    # must NEVER yield real behavior — only the visible per-call 'unconfigured' degradation.
    monkeypatch.setenv(ENV_CUBE_ENDPOINT, "http://cube.local:4000")
    monkeypatch.delenv(ENV_CUBEJS_API_SECRET_VALUE, raising=False)
    client = cube_client_from_env()
    assert isinstance(client, CubeClient)
    assert client.configured is False
    assert client.load(tenant_id=TENANT, query={})["status"] == "unconfigured"


# ---------------------------------------------------------------------------- tool injection seams


@pytest.mark.unit
def test_query_cube_uses_the_constructor_injected_client():
    transport = FakeTransport([(200, json.dumps({"data": [{"Deals.count": 3}]}).encode())])
    tool = QueryCube(cube_client=_client(transport))
    out = tool.invoke(ToolContext(tenant_id=TENANT), measures=["Deals.count"])
    assert out["status"] == "ok"
    assert out["result"]["rows"] == [{"Deals.count": 3}]
    assert "cube_status" not in out["result"]
    # The tenant the tool signed into the JWT is the ToolContext's (verified-claim) tenant.
    sent = decode_verified(transport.calls[0]["headers"]["Authorization"], SECRET, now=lambda: T0 + 1)
    assert sent["tenant_id"] == TENANT


@pytest.mark.unit
def test_query_cube_surfaces_unconfigured_status():
    out = QueryCube(cube_client=CubeClient()).invoke(ToolContext(tenant_id=TENANT))
    assert out["result"]["rows"] == []
    assert out["result"]["cube_status"] == "unconfigured"


@pytest.mark.unit
def test_query_cube_per_call_ctx_cube_wins_over_constructor_default():
    class CtxCube:
        def load(self, *, tenant_id, query):
            return [{"from": "ctx"}]  # plain-client shape (rows directly)

    injected = FakeTransport([(200, b'{"data": []}')])
    out = QueryCube(cube_client=_client(injected)).invoke(ToolContext(tenant_id=TENANT, cube=CtxCube()))
    assert out["result"]["rows"] == [{"from": "ctx"}]
    assert injected.calls == []  # the constructor default was never touched


@pytest.mark.unit
def test_query_cube_without_any_client_keeps_the_original_empty_result():
    out = QueryCube().invoke(ToolContext(tenant_id=TENANT))
    assert out["result"] == {"query": {"measures": [], "dimensions": []}, "rows": []}


@pytest.mark.unit
def test_build_view_lists_members_via_the_injected_client():
    meta = {"cubes": [{
        "name": "Deals",
        "measures": [{"name": "Deals.count"}, {"name": "Deals.pipeline_value"}],
        "dimensions": [],
    }]}
    transport = FakeTransport([(200, json.dumps(meta).encode())])
    seen = {}

    def gen(request, allowed_members, prev_error):
        seen["allowed"] = allowed_members
        return {
            "view_id": "v1", "title": "Pipeline", "semantic_refs": ["Deals.count"],
            "layout": [{"type": "kpi", "metric": "Deals.pipeline_value"}],
        }

    tool = BuildView(cube_client=_client(transport))
    out = tool.invoke(ToolContext(tenant_id=TENANT, extra={"generate_spec": gen}), request="pipeline")
    assert out["result"]["status"] == "valid"
    assert seen["allowed"] == ["Deals.count", "Deals.pipeline_value"]
    sent = decode_verified(transport.calls[0]["headers"]["Authorization"], SECRET, now=lambda: T0 + 1)
    assert sent["tenant_id"] == TENANT  # member catalog was fetched as the verified tenant
