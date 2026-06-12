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
        if ctx.hubspot is None:
            return {"object_types": [], "status": _NOT_CONNECTED}
        return {"object_types": list(ctx.hubspot.discover_object_types())}


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
        if ctx.hubspot is None:
            return {"object_type": object_type, "properties": [], "status": _NOT_CONNECTED}
        ps = ctx.hubspot.discover_properties(object_type)
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
        if ctx.hubspot is None:
            return {"object_type": object_type, "records": [], "status": _NOT_CONNECTED}
        records = ctx.hubspot.search_live(object_type, q=query, limit=limit)
        return {"object_type": object_type, "count": len(records), "records": _records_json(records)}


#: Read-only HubSpot live tools (AUTO). Write tools are ALWAYS_ASK / Greenlight (follow-up).
HUBSPOT_LIVE_TOOLS = (HubSpotObjectTypes, HubSpotProperties, HubSpotSearch)
