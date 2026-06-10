"""build_view tool (Build Guide Phase 7, Step 41).

A tool the chat calls. It resolves slots, asks Cube what metrics/dimensions exist, and prompts the
model to emit a spec that validates against the schema and references only real Cube members. Reject-
and-retry on validation failure — NEVER return unvalidated output. Read-only (AUTO): it produces a
declarative spec, it does not mutate anything.

Two generator shapes are accepted (ctx.extra['generate_spec'] wins over the injected default):

  1. A spec-generator object with `.generate(request=, allowed_members=)` returning
     `{"valid", "spec", "errors", "attempts"}` — e.g. `AnthropicSpecGenerator`
     (`agents/tools/spec_generator.py`), which runs its own validate + retry-once loop.
     build_view STILL re-validates the returned spec (defense in depth — never trust a
     generator's `valid` flag).
  2. A plain callable `(request=, allowed_members=, prev_error=) -> spec` (the original test
     seam) — build_view drives the reject-and-retry loop itself.

With neither configured, the original raise path stands: build_view without a generator is a
programming error, not a degraded mode. (The API wiring that injects the default generator rides
the next cycle — api/app.py and api/asgi.py are owned by the prod-deps lane this cycle.)
"""
from __future__ import annotations

from typing import Any

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

    def __init__(self, generator: Any = None) -> None:
        # Optional injected default spec generator (an AnthropicSpecGenerator instance). The
        # registry's no-arg `resolve()` keeps it None — preserving the original raise path.
        self._generator = generator

    def _execute(self, ctx: ToolContext, *, request: str) -> dict:
        # Catalog of real Cube members for this tenant (the model may reference only these).
        allowed = set(ctx.cube.members(tenant_id=ctx.tenant_id)) if ctx.cube else set()
        # The spec generator is an injected model call: per-call via ctx.extra, else the default.
        generate = ctx.extra.get("generate_spec") or self._generator
        if generate is None:
            raise RuntimeError("build_view requires ctx.extra['generate_spec'] (the model spec generator)")

        if hasattr(generate, "generate"):
            return self._run_generator(generate, request=request, allowed=allowed)
        return self._run_callable(generate, request=request, allowed=allowed)

    # ------------------------------------------------------------------ generator-object path
    def _run_generator(self, generator: Any, *, request: str, allowed: set[str]) -> dict:
        """Spec-generator contract: it validated + retried internally; we map and RE-verify."""
        out = generator.generate(request=request, allowed_members=sorted(allowed))
        attempts = out.get("attempts", 1)
        spec = out.get("spec")
        if out.get("valid") and isinstance(spec, dict):
            try:
                # Defense in depth: never render output build_view didn't validate itself.
                view_spec.validate(spec, allowed_members=allowed)
            except view_spec.ValidationError as e:
                return {"status": "invalid", "error": str(e), "attempts": attempts}
            return {"status": "valid", "spec": spec, "attempts": attempts}
        errors = [e for e in (out.get("errors") or []) if e]
        return {
            "status": "invalid",
            "error": "; ".join(errors) or "spec generation failed",
            "attempts": attempts,
        }

    # ------------------------------------------------------------------ plain-callable path
    def _run_callable(self, generate: Any, *, request: str, allowed: set[str]) -> dict:
        """Original seam: build_view drives the reject-and-retry loop around a bare model call."""
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
