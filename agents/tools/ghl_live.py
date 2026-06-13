"""Live GoHighLevel agent tools (AUTO / read-only): the "MCP" surface that lets an agent fetch GHL
data in real time, complementing the resident crm_records extract (source='gohighlevel').

Each tool runs against ``ctx.ghl`` — a ``GoHighLevelFullClient`` whose per-tenant OAuth token AND
location_id are already injected by the runtime (THE TRUST RULE: tenant comes from the verified claim,
never the tool args; the location rides the vaulted credential). All three are ``Policy.AUTO``
(read-only, auto-run). Write actions (create/update a contact/opportunity) are deliberately NOT here —
they are ``ALWAYS_ASK`` and route through Greenlight; a follow-up adds them. NO media blobs: values
(incl. call-recording / attachment URLs) are returned as text refs, never fetched.
"""
from __future__ import annotations

from .base import Policy, Tool, ToolContext

_NOT_CONNECTED = "not_connected"


def _client(ctx: ToolContext):
    """Resolve the tenant's GHL client off the context. `ctx.ghl` may be the client itself OR a
    zero-arg callable (lazy resolver) so a request that never calls a GHL tool pays no vault read.
    Returns None when GoHighLevel isn't connected (the tools then degrade honestly)."""
    g = ctx.ghl
    return g() if callable(g) else g


def _records_json(records) -> list[dict]:
    return [
        {"id": r.source_ref_id, "properties": r.properties, "associations": r.associations}
        for r in records
    ]


class GhlObjectTypes(Tool):
    name = "ghl_object_types"
    description = "List the GoHighLevel object types available to query live (standard + custom)."
    input_schema = {"type": "object", "properties": {}}
    policy = Policy.AUTO

    def _execute(self, ctx: ToolContext, **_kw) -> dict:
        g = _client(ctx)
        if g is None:
            return {"object_types": [], "status": _NOT_CONNECTED}
        return {"object_types": list(g.discover_object_types())}


class GhlFields(Tool):
    name = "ghl_fields"
    description = "List the field names for a GoHighLevel object type (standard + custom fields)."
    input_schema = {
        "type": "object",
        "properties": {"object_type": {"type": "string"}},
        "required": ["object_type"],
    }
    policy = Policy.AUTO

    def _execute(self, ctx: ToolContext, *, object_type: str) -> dict:
        g = _client(ctx)
        if g is None:
            return {"object_type": object_type, "fields": [], "status": _NOT_CONNECTED}
        return {"object_type": object_type, "fields": list(g.discover_fields(object_type))}


class GhlSearch(Tool):
    name = "ghl_search"
    description = (
        "Live-search a GoHighLevel object type (read-only). Returns up to `limit` records; "
        "call-recording / attachment values are URL refs only, never the bytes."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "object_type": {"type": "string", "description": "e.g. contacts, opportunities, conversations"},
            "query": {"type": "string", "description": "optional full-text query"},
            "limit": {"type": "integer", "description": "max records (default 10, cap 100)"},
        },
        "required": ["object_type"],
    }
    policy = Policy.AUTO

    def _execute(self, ctx: ToolContext, *, object_type: str, query: str | None = None,
                 limit: int = 10) -> dict:
        g = _client(ctx)
        if g is None:
            return {"object_type": object_type, "records": [], "status": _NOT_CONNECTED}
        records = g.search_live(object_type, q=query, limit=limit)
        return {"object_type": object_type, "count": len(records), "records": _records_json(records)}


#: Read-only GoHighLevel live tools (AUTO). Write tools are ALWAYS_ASK / Greenlight (follow-up).
GHL_LIVE_TOOLS = (GhlObjectTypes, GhlFields, GhlSearch)
