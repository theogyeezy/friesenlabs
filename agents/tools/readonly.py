"""Read-only tools (AUTO policy): search_rag, query_cube, read_crm, run_model.

Each uses injected clients from ToolContext and runs only after bind_tenant() (RLS applies).
"""
from __future__ import annotations

from typing import Any

from .base import Policy, Tool, ToolContext


class SearchRag(Tool):
    name = "search_rag"
    description = "Semantic search the tenant corpus (pgvector, tenant-scoped)."
    input_schema = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
    policy = Policy.AUTO

    def _execute(self, ctx: ToolContext, *, q: str) -> dict:
        hits = ctx.rag.search(tenant_id=ctx.tenant_id, query=q) if ctx.rag else []
        return {"query": q, "hits": hits}


class QueryCube(Tool):
    name = "query_cube"
    description = "Query governed metrics via Cube (tenant security context enforced)."
    input_schema = {
        "type": "object",
        "properties": {"measures": {"type": "array"}, "dimensions": {"type": "array"}},
    }
    policy = Policy.AUTO

    def __init__(self, cube_client: Any = None) -> None:
        # Optional injected default Cube client (agents/tools/cube_client.CubeClient — mints the
        # per-request tenant JWT from the verified claim). The registry's no-arg `resolve()` keeps
        # it None; a per-call ctx.cube always wins over the constructor default.
        self._cube_client = cube_client

    def _execute(self, ctx: ToolContext, *, measures=None, dimensions=None) -> dict:
        query = {"measures": measures or [], "dimensions": dimensions or []}
        # Cube client carries the tenant security context; never write a tenant filter by hand.
        cube = ctx.cube if ctx.cube is not None else self._cube_client
        if cube is None:
            return {"query": query, "rows": []}
        result = cube.load(tenant_id=ctx.tenant_id, query=query)
        if isinstance(result, dict) and "rows" in result:
            # CubeClient shape: {"status", "rows", ...} — surface non-ok degradations
            # ('unconfigured'/'error') so the agent sees WHY rows are empty, never a silent [].
            out = {"query": query, "rows": result.get("rows") or []}
            status = result.get("status")
            if status and status != "ok":
                out["cube_status"] = status
                detail = result.get("error") or result.get("detail")
                if detail:
                    out["detail"] = detail
            return out
        # Plain-client shape (tests/fakes): load() returned the rows themselves.
        return {"query": query, "rows": result}


class ReadCrm(Tool):
    name = "read_crm"
    description = "Read contacts/deals for the tenant."
    input_schema = {
        "type": "object",
        "properties": {"entity": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["entity"],
    }
    policy = Policy.AUTO

    def _execute(self, ctx: ToolContext, *, entity: str, limit: int = 50) -> dict:
        rows = ctx.db.read(entity=entity, limit=limit) if ctx.db else []
        return {"entity": entity, "rows": rows}
