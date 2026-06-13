"""Unit tests for the live GoHighLevel agent tools (agents/tools/ghl_live.py).

No network: a FakeGhl stands in for the per-tenant GoHighLevelFullClient on ctx.ghl. Asserts
read-only AUTO policy, valid tool specs, arg-threading via ctx, the not-connected degradation, and
the registry/executor/roster wiring.
"""
import pytest

from agents.tools.base import Policy, ToolContext
from agents.tools.ghl_live import GHL_LIVE_TOOLS, GhlFields, GhlObjectTypes, GhlSearch
from ingest.connectors.hubspot_full import Record  # source-agnostic record shape

pytestmark = pytest.mark.unit


class FakeGhl:
    """Stands in for a tenant's token+location-set GoHighLevelFullClient on ctx.ghl."""

    def discover_object_types(self):
        return ("contacts", "opportunities", "conversations", "custom_pet")

    def discover_fields(self, object_type):
        return ("firstName", "cf_cf1")

    def search_live(self, object_type, *, q=None, limit=10):
        self.last = {"object_type": object_type, "q": q, "limit": limit}
        return [Record(object_type, "1", {"firstName": "Ada"}, {"company": ["100"]}, "t1")]


def _ctx(ghl=None):
    return ToolContext(tenant_id="tenant-A", ghl=ghl)


def test_all_live_tools_are_read_only_auto_with_valid_specs():
    for tool_cls in GHL_LIVE_TOOLS:
        assert tool_cls.policy is Policy.AUTO            # read-only: auto-run, never Greenlight
        spec = tool_cls().to_spec()
        assert spec and tool_cls.name and tool_cls.input_schema["type"] == "object"


def test_object_types_tool_lists_standard_and_custom():
    out = GhlObjectTypes().invoke(_ctx(FakeGhl()))
    assert out["status"] == "ok"
    types = out["result"]["object_types"]
    assert "contacts" in types and "custom_pet" in types


def test_fields_tool_lists_standard_and_custom_fields():
    out = GhlFields().invoke(_ctx(FakeGhl()), object_type="contacts")
    res = out["result"]
    assert "firstName" in res["fields"] and "cf_cf1" in res["fields"]


def test_search_tool_returns_records_and_threads_args():
    g = FakeGhl()
    out = GhlSearch().invoke(_ctx(g), object_type="contacts", query="ada", limit=5)
    res = out["result"]
    assert res["count"] == 1
    assert res["records"][0] == {
        "id": "1", "properties": {"firstName": "Ada"}, "associations": {"company": ["100"]}}
    assert g.last == {"object_type": "contacts", "q": "ada", "limit": 5}  # args threaded through


def test_tools_degrade_honestly_when_not_connected():
    assert GhlSearch().invoke(_ctx(None), object_type="contacts")["result"]["status"] == "not_connected"
    assert GhlObjectTypes().invoke(_ctx(None))["result"]["status"] == "not_connected"
    assert GhlFields().invoke(_ctx(None), object_type="opportunities")["result"]["status"] == "not_connected"


def test_tool_resolves_lazy_callable_client_once_on_invoke():
    calls = []

    def resolver():               # ctx.ghl may be a lazy zero-arg resolver
        calls.append(1)
        return FakeGhl()

    out = GhlSearch().invoke(_ctx(resolver), object_type="contacts")
    assert out["result"]["count"] == 1
    assert calls == [1]           # resolved exactly once, only when the tool ran


# --- registry + per-tenant resolver wiring (item 5) ---------------------- #
def test_registry_includes_the_live_ghl_tools():
    from agents.tools.registry import TOOL_REGISTRY

    for name in ("ghl_object_types", "ghl_fields", "ghl_search"):
        assert name in TOOL_REGISTRY
        assert TOOL_REGISTRY[name].policy is Policy.AUTO  # read-only, never Greenlight


def test_tenant_ghl_client_is_none_when_not_connected():
    from agents.tools.registry import tenant_ghl_client

    class NoCreds:
        def get_secret(self, ref):
            raise KeyError("no vaulted credential")

    assert tenant_ghl_client("tenant-A", NoCreds()) is None  # honest not-connected


def test_tenant_ghl_client_returns_token_and_location_set_client_when_connected():
    from agents.tools.registry import tenant_ghl_client
    from ingest.connectors.gohighlevel_full import GoHighLevelFullClient

    class PastedJson:
        def get_secret(self, ref):
            return '{"token": "pasted-bearer-xyz", "location_id": "loc-7"}'

    client = tenant_ghl_client("tenant-A", PastedJson())
    assert isinstance(client, GoHighLevelFullClient)
    assert client._token == "pasted-bearer-xyz"   # token threaded from the vault
    assert client._location_id == "loc-7"         # location threaded from the vault


# --- end-to-end through the executor + roster grant (item 5) ------------- #
def test_executor_dispatches_live_ghl_tool_for_the_bound_tenant():
    from api import asgi
    from api.control.types import Action

    seen = []

    class FakeG:
        def search_live(self, object_type, *, q=None, limit=10):
            return [Record(object_type, "1", {"firstName": "Ada"}, {}, "t1")]

    def resolver(tenant_id):
        seen.append(tenant_id)
        return FakeG()

    executor = asgi.make_executor(ghl_resolver=resolver)
    out = executor(Action(name="ghl_search", tenant_id="tenant-A",
                          payload={"object_type": "contacts", "query": "ada"}))
    assert out["status"] == "ok"
    assert out["result"]["count"] == 1 and out["result"]["records"][0]["id"] == "1"
    assert seen == ["tenant-A"]  # resolver invoked with the action's BOUND tenant (trust rule)


def test_executor_ghl_tool_degrades_when_not_connected_or_unwired():
    from api import asgi
    from api.control.types import Action

    # resolver returns None (tenant not connected)
    ex1 = asgi.make_executor(ghl_resolver=lambda _tid: None)
    out1 = ex1(Action(name="ghl_search", tenant_id="t", payload={"object_type": "contacts"}))
    assert out1["result"]["status"] == "not_connected"
    # no resolver wired at all (additive default) — still honest, never crashes
    ex2 = asgi.make_executor()
    out2 = ex2(Action(name="ghl_search", tenant_id="t", payload={"object_type": "contacts"}))
    assert out2["result"]["status"] == "not_connected"


def test_scout_roster_grants_the_live_ghl_tools():
    from agents.roster import SCOUT

    for name in ("ghl_search", "ghl_fields", "ghl_object_types"):
        assert name in SCOUT.tools  # exposed to the research specialist
