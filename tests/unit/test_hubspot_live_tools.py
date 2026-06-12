"""Unit tests for the live HubSpot agent tools (agents/tools/hubspot_live.py).

No network: a FakeHubSpot stands in for the per-tenant HubSpotFullClient on ctx.hubspot. Asserts
read-only AUTO policy, valid tool specs, token-threading via ctx, media flagging, and the
not-connected degradation.
"""
import pytest

from agents.tools.base import Policy, ToolContext
from agents.tools.hubspot_live import (
    HUBSPOT_LIVE_TOOLS,
    HubSpotObjectTypes,
    HubSpotProperties,
    HubSpotSearch,
)
from ingest.connectors.hubspot_full import PropertySet, Record

pytestmark = pytest.mark.unit


class FakeHubSpot:
    """Stands in for a tenant's token-set HubSpotFullClient on ctx.hubspot."""

    def discover_object_types(self):
        return ("contacts", "companies", "deals", "p12345_custom")

    def discover_properties(self, object_type):
        return PropertySet(("email", "headshot"), frozenset({"headshot"}))

    def search_live(self, object_type, *, q=None, limit=10):
        self.last = {"object_type": object_type, "q": q, "limit": limit}
        return [Record(object_type, "1", {"email": "a@x.com"}, {"companies": ["100"]}, "t1")]


def _ctx(hubspot=None):
    return ToolContext(tenant_id="tenant-A", hubspot=hubspot)


def test_all_live_tools_are_read_only_auto_with_valid_specs():
    for tool_cls in HUBSPOT_LIVE_TOOLS:
        assert tool_cls.policy is Policy.AUTO            # read-only: auto-run, never Greenlight
        spec = tool_cls().to_spec()
        assert spec and tool_cls.name and tool_cls.input_schema["type"] == "object"


def test_object_types_tool_lists_standard_and_custom():
    out = HubSpotObjectTypes().invoke(_ctx(FakeHubSpot()))
    assert out["status"] == "ok"
    types = out["result"]["object_types"]
    assert "contacts" in types and "p12345_custom" in types


def test_properties_tool_flags_media():
    out = HubSpotProperties().invoke(_ctx(FakeHubSpot()), object_type="contacts")
    res = out["result"]
    assert "email" in res["properties"]
    assert res["media"] == ["headshot"]            # file/media property flagged (URL-only)


def test_search_tool_returns_records_and_threads_args():
    hs = FakeHubSpot()
    out = HubSpotSearch().invoke(_ctx(hs), object_type="contacts", query="acme", limit=5)
    res = out["result"]
    assert res["count"] == 1
    assert res["records"][0] == {
        "id": "1", "properties": {"email": "a@x.com"}, "associations": {"companies": ["100"]}}
    assert hs.last == {"object_type": "contacts", "q": "acme", "limit": 5}  # args threaded through


def test_tools_degrade_honestly_when_not_connected():
    assert HubSpotSearch().invoke(_ctx(None), object_type="contacts")["result"]["status"] == "not_connected"
    assert HubSpotObjectTypes().invoke(_ctx(None))["result"]["status"] == "not_connected"
    assert HubSpotProperties().invoke(_ctx(None), object_type="deals")["result"]["status"] == "not_connected"


def test_tool_resolves_lazy_callable_client_once_on_invoke():
    calls = []

    def resolver():               # ctx.hubspot may be a lazy zero-arg resolver
        calls.append(1)
        return FakeHubSpot()

    out = HubSpotSearch().invoke(_ctx(resolver), object_type="contacts")
    assert out["result"]["count"] == 1
    assert calls == [1]           # resolved exactly once, only when the tool ran


# --- registry + per-tenant resolver wiring (item 11) --------------------- #
def test_registry_includes_the_live_hubspot_tools():
    from agents.tools.registry import TOOL_REGISTRY

    for name in ("hubspot_object_types", "hubspot_properties", "hubspot_search"):
        assert name in TOOL_REGISTRY
        assert TOOL_REGISTRY[name].policy is Policy.AUTO  # read-only, never Greenlight


def test_tenant_hubspot_client_is_none_when_not_connected():
    from agents.tools.registry import tenant_hubspot_client

    class NoCreds:
        def get_secret(self, ref):
            raise KeyError("no vaulted credential")

    assert tenant_hubspot_client("tenant-A", NoCreds()) is None  # honest not-connected


def test_tenant_hubspot_client_returns_token_set_client_when_connected():
    from agents.tools.registry import tenant_hubspot_client
    from ingest.connectors.hubspot_full import HubSpotFullClient

    class PastedToken:
        def get_secret(self, ref):
            return "pasted-bearer-xyz"  # a bare pasted token

    client = tenant_hubspot_client("tenant-A", PastedToken())
    assert isinstance(client, HubSpotFullClient)
    assert client._token == "pasted-bearer-xyz"  # token threaded from the vault
