"""Live HubSpot agent tools (AUTO / read-only): the "MCP" surface that lets an agent fetch HubSpot
data in real time, complementing the resident crm_records extract.

Each tool runs against ``ctx.hubspot`` — a ``HubSpotFullClient`` whose per-tenant OAuth token is
already injected by the runtime (THE TRUST RULE: tenant comes from the verified claim, never the
tool args). All three are ``Policy.AUTO`` (read-only, auto-run). Write actions (create/update a
contact/deal) are deliberately NOT here — they are ``ALWAYS_ASK`` and route through Greenlight; a
follow-up adds them. NO media blobs: values (incl. file URLs) are returned as text, never fetched.
"""
from __future__ import annotations

from .base import Policy, Tool, ToolContext

_NOT_CONNECTED = "not_connected"


def _client(ctx: ToolContext):
    """Resolve the tenant's HubSpot client off the context. `ctx.hubspot` may be the client itself
    OR a zero-arg callable (lazy resolver) so a request that never calls a HubSpot tool pays no
    vault read. Returns None when HubSpot isn't connected (the tools then degrade honestly)."""
    hs = ctx.hubspot
    return hs() if callable(hs) else hs


def _records_json(records) -> list[dict]:
    return [
        {"id": r.source_ref_id, "properties": r.properties, "associations": r.associations}
        for r in records
    ]


class HubSpotObjectTypes(Tool):
    name = "hubspot_object_types"
    description = "List the HubSpot object types available to query live (standard + custom)."
    input_schema = {"type": "object", "properties": {}}
    policy = Policy.AUTO

    def _execute(self, ctx: ToolContext, **_kw) -> dict:
        hs = _client(ctx)
        if hs is None:
            return {"object_types": [], "status": _NOT_CONNECTED}
        return {"object_types": list(hs.discover_object_types())}


class HubSpotProperties(Tool):
    name = "hubspot_properties"
    description = "List all property names for a HubSpot object type (media properties flagged)."
    input_schema = {
        "type": "object",
        "properties": {"object_type": {"type": "string"}},
        "required": ["object_type"],
    }
    policy = Policy.AUTO

    def _execute(self, ctx: ToolContext, *, object_type: str) -> dict:
        hs = _client(ctx)
        if hs is None:
            return {"object_type": object_type, "properties": [], "status": _NOT_CONNECTED}
        ps = hs.discover_properties(object_type)
        return {"object_type": object_type, "properties": list(ps.names), "media": sorted(ps.media)}


class HubSpotSearch(Tool):
    name = "hubspot_search"
    description = (
        "Live-search a HubSpot object type (read-only). Returns up to `limit` records; file/media "
        "values are URL refs only, never the bytes."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "object_type": {"type": "string", "description": "e.g. contacts, companies, deals, tickets"},
            "query": {"type": "string", "description": "optional full-text query"},
            "limit": {"type": "integer", "description": "max records (default 10, cap 100)"},
        },
        "required": ["object_type"],
    }
    policy = Policy.AUTO

    def _execute(self, ctx: ToolContext, *, object_type: str, query: str | None = None,
                 limit: int = 10) -> dict:
        hs = _client(ctx)
        if hs is None:
            return {"object_type": object_type, "records": [], "status": _NOT_CONNECTED}
        records = hs.search_live(object_type, q=query, limit=limit)
        return {"object_type": object_type, "count": len(records), "records": _records_json(records)}


#: Read-only HubSpot live tools (AUTO). Write tools are ALWAYS_ASK / Greenlight (follow-up).
HUBSPOT_LIVE_TOOLS = (HubSpotObjectTypes, HubSpotProperties, HubSpotSearch)
