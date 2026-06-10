"""Unit: AnthropicAdminClient — idempotent ensure_workspace, headers, key/limits/archive shapes.

All transport-mocked at the urllib-opener seam; no network at import or in any test.
"""
import io
import json
import urllib.error
from urllib.parse import parse_qs, urlsplit

import pytest

from shared import config
from signup.anthropic_admin import (
    API_VERSION,
    AdminApiError,
    AnthropicAdminClient,
)


class FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw


class FakeAdminApi:
    """Stateful in-memory Admin API behind the injectable opener seam."""

    def __init__(self):
        self.workspaces = []   # [{"id", "name"}]
        self.key_creates = []  # captured POST /api_keys bodies
        self.archived = []     # workspace ids archived
        self.requests = []     # every urllib Request seen

    def __call__(self, request, timeout):
        self.requests.append(request)
        method = request.get_method()
        parts = urlsplit(request.full_url)
        path = parts.path
        if method == "GET" and path == "/v1/organizations/workspaces":
            q = parse_qs(parts.query)
            assert q.get("include_archived") == ["false"]
            return FakeResponse({"data": list(self.workspaces), "has_more": False,
                                 "first_id": None, "last_id": None})
        if method == "POST" and path == "/v1/organizations/workspaces":
            body = json.loads(request.data)
            ws = {"id": f"wrkspc_{len(self.workspaces) + 1:02d}", "name": body["name"],
                  "type": "workspace"}
            self.workspaces.append(ws)
            return FakeResponse(ws)
        if method == "POST" and path == "/v1/organizations/api_keys":
            body = json.loads(request.data)
            self.key_creates.append(body)
            return FakeResponse({"id": "apikey_01", "name": body["name"],
                                 "key": f"sk-ant-api03-{body['workspace_id']}"})
        if method == "POST" and path.startswith("/v1/organizations/workspaces/") \
                and path.endswith("/archive"):
            ws_id = path.split("/")[-2]
            self.archived.append(ws_id)
            return FakeResponse({"id": ws_id, "type": "workspace",
                                 "archived_at": "2026-06-09T00:00:00Z"})
        if method == "POST" and path.endswith("/limits"):
            return FakeResponse({"ok": True})
        raise AssertionError(f"unexpected {method} {request.full_url}")

    def create_calls(self):
        return [r for r in self.requests
                if r.get_method() == "POST"
                and urlsplit(r.full_url).path == "/v1/organizations/workspaces"]


def _client(api=None, key="sk-ant-admin-test"):
    return AnthropicAdminClient(key, opener=api or FakeAdminApi())


# ---------------- clean errors when unconfigured (no network) ----------------
@pytest.mark.unit
def test_unconfigured_key_raises_clean_error_before_any_network():
    api = FakeAdminApi()
    client = AnthropicAdminClient("", opener=api)
    for call in (
        lambda: client.ensure_workspace("t1"),
        lambda: client.create_workspace_key("wrkspc_01", "t1"),
        lambda: client.set_limits("wrkspc_01", "t1"),
        lambda: client.delete_workspace("wrkspc_01"),
    ):
        with pytest.raises(AdminApiError) as e:
            call()
        assert "not configured" in str(e.value)
    assert api.requests == []  # refused before any transport


@pytest.mark.unit
def test_admin_secret_ref_is_a_name_not_a_value():
    c = config.load()
    assert c.anthropic_admin_key_secret.startswith("uplift/")
    assert not c.anthropic_admin_key_secret.startswith("sk-ant-")


# ---------------- idempotent ensure_workspace ----------------
@pytest.mark.unit
def test_ensure_workspace_creates_then_reuses_by_name():
    api = FakeAdminApi()
    client = _client(api)
    ws1 = client.ensure_workspace("tenant-a1")
    ws2 = client.ensure_workspace("tenant-a1")  # re-delivered webhook path
    assert ws1 == ws2 == "wrkspc_01"
    assert len(api.create_calls()) == 1  # exactly one create — second call found it by name
    assert api.workspaces[0]["name"] == "uplift-tenant-tenant-a1"


@pytest.mark.unit
def test_ensure_workspace_distinct_tenants_get_distinct_workspaces():
    api = FakeAdminApi()
    client = _client(api)
    assert client.ensure_workspace("t1") != client.ensure_workspace("t2")
    assert len(api.create_calls()) == 2


@pytest.mark.unit
def test_ensure_workspace_finds_preexisting_without_creating():
    api = FakeAdminApi()
    api.workspaces.append({"id": "wrkspc_99", "name": "uplift-tenant-t9", "type": "workspace"})
    client = _client(api)
    assert client.ensure_workspace("t9") == "wrkspc_99"
    assert api.create_calls() == []


# ---------------- headers on every request ----------------
@pytest.mark.unit
def test_admin_headers_on_every_request():
    api = FakeAdminApi()
    client = _client(api, key="sk-ant-admin-xyz")
    client.ensure_workspace("t1")
    client.create_workspace_key("wrkspc_01", "t1")
    client.delete_workspace("wrkspc_01")
    assert len(api.requests) >= 3
    for request in api.requests:
        assert request.get_header("X-api-key") == "sk-ant-admin-xyz"
        assert request.get_header("Anthropic-version") == API_VERSION


# ---------------- create_workspace_key (VERIFY-flagged endpoint) ----------------
@pytest.mark.unit
def test_create_workspace_key_payload_and_returned_secret():
    api = FakeAdminApi()
    client = _client(api)
    key = client.create_workspace_key("wrkspc_07", "tenant-a1")
    assert key == "sk-ant-api03-wrkspc_07"
    (body,) = api.key_creates
    assert body == {"name": "uplift-tenant-a1", "workspace_id": "wrkspc_07"}


@pytest.mark.unit
def test_create_workspace_key_missing_secret_material_is_an_error():
    def opener(request, timeout):
        return FakeResponse({"id": "apikey_01", "name": "x"})  # no key field

    client = AnthropicAdminClient("sk-ant-admin-test", opener=opener)
    with pytest.raises(AdminApiError) as e:
        client.create_workspace_key("wrkspc_01", "t1")
    assert "no key material" in str(e.value)


# ---------------- set_limits soft-fail (unverified write endpoint) ----------------
@pytest.mark.unit
def test_set_limits_posts_to_workspace_limits_path():
    api = FakeAdminApi()
    client = _client(api)
    assert client.set_limits("wrkspc_03", "t1") is True
    request = api.requests[-1]
    assert urlsplit(request.full_url).path == "/v1/organizations/workspaces/wrkspc_03/limits"
    assert json.loads(request.data)  # a non-empty limits body was sent


@pytest.mark.unit
def test_set_limits_http_failure_soft_fails_not_raises(caplog):
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "not found", None,
                                     io.BytesIO(b'{"error": "no such endpoint"}'))

    client = AnthropicAdminClient("sk-ant-admin-test", opener=opener)
    with caplog.at_level("WARNING"):
        assert client.set_limits("wrkspc_01", "t1") is False  # provisioning keeps going
    assert any("soft-failed" in r.message for r in caplog.records)


# ---------------- delete_workspace == archive ----------------
@pytest.mark.unit
def test_delete_workspace_archives():
    api = FakeAdminApi()
    client = _client(api)
    assert client.delete_workspace("wrkspc_05") == "wrkspc_05"
    assert api.archived == ["wrkspc_05"]
    request = api.requests[-1]
    assert urlsplit(request.full_url).path == "/v1/organizations/workspaces/wrkspc_05/archive"
    assert request.get_method() == "POST"


# ---------------- HTTP errors surface as AdminApiError with detail ----------------
@pytest.mark.unit
def test_http_error_raises_admin_api_error_with_detail():
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", None,
                                     io.BytesIO(b'{"error": "invalid x-api-key"}'))

    client = AnthropicAdminClient("sk-ant-admin-bad", opener=opener)
    with pytest.raises(AdminApiError) as e:
        client.ensure_workspace("t1")
    msg = str(e.value)
    assert "HTTP 401" in msg
    assert "invalid x-api-key" in msg


# ---------------- import safety ----------------
@pytest.mark.unit
def test_import_is_side_effect_free():
    import importlib

    import signup.anthropic_admin as mod

    importlib.reload(mod)  # re-import performs no I/O
