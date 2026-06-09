"""build_view tool (Build Guide Phase 7, Step 41).

A tool the chat calls. It resolves slots, asks Cube what metrics/dimensions exist, and prompts the
model to emit a spec that validates against the schema and references only real Cube members. Reject-
and-retry on validation failure — NEVER return unvalidated output. Read-only (AUTO): it produces a
declarative spec, it does not mutate anything.
"""
from __future__ import annotations

from shared import view_spec

from .base import Policy, Tool, ToolContext

MAX_ATTEMPTS = 3


class BuildView(Tool):
    name = "build_view"
    description = "Generate a validated dashboard view-spec (declarative; never code)."
    input_schema = {
        "type": "object",
        "properties": {"request": {"type": "string"}},
        "required": ["request"],
    }
    policy = Policy.AUTO

    def _execute(self, ctx: ToolContext, *, request: str) -> dict:
        # Catalog of real Cube members for this tenant (the model may reference only these).
        allowed = set(ctx.cube.members(tenant_id=ctx.tenant_id)) if ctx.cube else set()
        # The spec generator is an injected model call (ctx.extra['generate_spec']); fake in tests.
        generate = ctx.extra.get("generate_spec")
        if generate is None:
            raise RuntimeError("build_view requires ctx.extra['generate_spec'] (the model spec generator)")

        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            spec = generate(request=request, allowed_members=sorted(allowed), prev_error=last_error)
            try:
                view_spec.validate(spec, allowed_members=allowed)
                return {"status": "valid", "spec": spec, "attempts": attempt}
            except view_spec.ValidationError as e:
                last_error = str(e)  # feed the error back so the next attempt can self-correct

        # Never render unvalidated output.
        return {"status": "invalid", "error": last_error, "attempts": MAX_ATTEMPTS}
