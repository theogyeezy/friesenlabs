"""AnthropicViewPatcher — the real (Sonnet-backed) view-spec EDITOR (NL refine).

The sibling of `agents.tools.spec_generator.AnthropicSpecGenerator`: where the generator builds a
NEW view-spec from an ask (the Balto CREATE path), this PATCHES an EXISTING, already-validated
view-spec given a plain-English instruction ("make it a line chart for the last 90 days",
"break it down by owner") — the EDIT path behind `POST /views/{id}/refine`.

Contract (deliberately the `Callable[[dict, str], dict]` the route + `SavedViews.refine_nl`
expect): `patcher(spec, instruction) -> patched_spec`. It returns a spec that has passed the SAME
two-gate validation the generator uses (JSON schema + allowed-member catalog) with the SAME
reject-and-retry-MAX_ATTEMPTS loop (the validator's words fed back to the model). The store then
re-validates against THAT tenant's live Cube members before persisting (defense in depth — same
posture as `BuildView` re-validating a generator's output).

SPEC, NOT CODE — CLAUDE.md hard constraint #7: the model only ever emits declarative catalog
blocks (kpi / chart / table / …) bound to governed Cube members, never executable code.

Unlike the generator's dict-returning `generate(...)`, this RAISES on failure: the route
(`api/app.py refine_view`) wraps the call in `except Exception -> 422`, so an unpatchable ask
surfaces as an honest 422 ("that didn't produce a valid chart") rather than a half-applied spec.
A `ViewPatchError` is raised when the patcher is unconfigured or every attempt fails validation —
the route NEVER persists an unvalidated spec.

Offline/import-safe: the `anthropic` SDK is imported lazily on first use (same seam as the
generator + `conv.synthesizer`); constructing the class needs no network and no creds. Tests
inject a fake `client`. In `api/asgi.py` the patcher is built ONLY when the org Anthropic key is
present — without it `ApiDeps.view_patcher` stays None and the route keeps its honest 501.

Note on the beta header: this is a plain `/v1/messages` call, so the Managed Agents beta header is
NOT required (same rationale as the generator / `conv/synthesizer.py`).
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterable

from agents.roster import SONNET
from shared import view_spec
from shared.config import ENV_ANTHROPIC_API_KEY

# Reuse the generator's defensive JSON parsing verbatim — same model, same output shape.
from agents.tools.spec_generator import _parse_spec

# One patch + one reject-and-retry with the validator errors fed back (mirror the generator).
MAX_ATTEMPTS = 2

UNCONFIGURED_ERROR = (
    "view patcher unconfigured: no Anthropic client or API key "
    f"(inject client= for tests, or set {ENV_ANTHROPIC_API_KEY})"
)


class ViewPatchError(Exception):
    """Raised when an NL refine cannot produce a valid patched spec (route -> 422)."""


# Stable system prompt (schema first, deterministic serialization — cache-friendly). The volatile
# parts (the current spec, the instruction, the members, the validator feedback) go in the user
# message. Edit-flavored: keep what the instruction does not touch; only emit catalog blocks.
_SYSTEM = (
    "You EDIT dashboard view-specs for a multi-tenant CRM. You are given an EXISTING, already-valid "
    "view-spec and a plain-English instruction describing a change to it. A view-spec is "
    "DECLARATIVE DATA, never code: no React/JS/HTML, only catalog blocks (kpi / chart / table) "
    "bound to governed Cube members.\n\n"
    "Respond with ONLY a JSON object — the FULL updated view-spec — that validates against this "
    "JSON schema:\n\n"
    + json.dumps(view_spec.SCHEMA, indent=2, sort_keys=True)
    + "\n\nRules:\n"
    "- Apply the instruction to the given spec; keep everything the instruction does not change.\n"
    "- Keep the SAME 'view_id' as the given spec (this is an edit, not a new view).\n"
    "- Reference ONLY Cube members from the provided 'Available Cube members' list "
    "(Cube.field form); list every member you use in 'semantic_refs'.\n"
    "- Output the complete patched spec as raw JSON only — no prose, no markdown fences, no "
    "executable code of any kind."
)


class AnthropicViewPatcher:
    """Sonnet-backed view-spec EDITOR with schema+catalog validation and one retry.

    Callable surface `(spec, instruction) -> patched_spec` so it drops straight into
    `ApiDeps.view_patcher` and `SavedViews.refine_nl(..., patcher)`.

    Lazy client: nothing touches the network at import or construction time. Inject `client`
    (any object with `.messages.create(...)`) in tests, or `api_key` for an explicit key — with
    neither, the SDK default credential resolution (ANTHROPIC_API_KEY env) applies on first use.

    `allowed_members`: the governed Cube member catalog the patched spec is checked against. None
    (the catalog file isn't shipped in the API image — the live pre-catalog state) => member
    validation is SKIPPED, exactly the `SavedViews(allowed_members=None)` posture, and the store's
    own re-validation remains the final gate. When the patched spec references a member, only the
    given catalog can disprove it; the existing spec's members are folded in so an edit that merely
    keeps an existing reference is never spuriously rejected by a partial catalog.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any = None,
        model: str = SONNET,
        max_tokens: int = 4096,
        max_attempts: int = MAX_ATTEMPTS,
        allowed_members: Iterable[str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._client = client  # built lazily; tests inject a fake
        self.model = model
        self.max_tokens = max_tokens
        self.max_attempts = max(1, max_attempts)
        self._allowed_members = set(allowed_members) if allowed_members is not None else None

    @property
    def configured(self) -> bool:
        """True when a model call is even possible (injected client, explicit key, or env key)."""
        return (
            self._client is not None
            or bool(self._api_key)
            or bool(os.environ.get(ENV_ANTHROPIC_API_KEY))
        )

    def _client_or_build(self) -> Any:
        if self._client is None:
            from anthropic import Anthropic  # noqa: PLC0415 — lazy on purpose (import-safety)

            self._client = Anthropic(api_key=self._api_key)
        return self._client

    def _allowed_for(self, spec: dict) -> set[str] | None:
        """The member catalog to check a patch against: the configured catalog UNION the existing
        spec's own already-validated members (so keeping a prior reference never trips a partial
        catalog). None when no catalog is configured -> schema-only validation (store re-checks)."""
        if self._allowed_members is None:
            return None
        existing = set(spec.get("semantic_refs") or []) if isinstance(spec, dict) else set()
        return self._allowed_members | existing

    # ------------------------------------------------------------------ callable contract
    def __call__(self, spec: dict, instruction: str) -> dict:
        """Patch `spec` per `instruction`; return a validated patched spec or raise ViewPatchError.

        Validation (JSON schema + member check) runs on EVERY attempt; a failing attempt's errors
        are fed back to the model for the single retry. Never returns an unvalidated spec.
        """
        if not self.configured:
            raise ViewPatchError(UNCONFIGURED_ERROR)
        if not isinstance(spec, dict):
            raise ViewPatchError("view patcher needs an existing view-spec to edit")

        allowed = self._allowed_for(spec)
        members = sorted(allowed) if allowed is not None else None
        errors: list[str] = []
        prev_error: str | None = None
        for _attempt in range(1, self.max_attempts + 1):
            try:
                raw = self._call_model(spec, instruction, members, prev_error)
            except Exception as e:  # API error -> record, feed back, retry (never crash a turn)
                prev_error = f"model call failed: {e}"
                errors.append(prev_error)
                continue

            patched = _parse_spec(raw)
            if patched is None:
                prev_error = "model output was not a JSON object (emit the raw view-spec JSON only)"
                errors.append(prev_error)
                continue

            try:
                view_spec.validate(patched, allowed_members=allowed)
            except view_spec.ValidationError as e:
                prev_error = str(e)  # the validator's words go back to the model verbatim
                errors.append(prev_error)
                continue

            return patched

        # Reject-and-retry exhausted — never return unvalidated output.
        raise ViewPatchError(
            "view refine failed validation after "
            f"{self.max_attempts} attempts: {'; '.join(errors)}"
        )

    # ------------------------------------------------------------------ model call
    def _call_model(
        self, spec: dict, instruction: str, members: list[str] | None, prev_error: str | None
    ) -> str:
        user = (
            f"Existing view-spec to edit:\n{json.dumps(spec, sort_keys=True)}\n\n"
            f"Instruction:\n{instruction}"
        )
        if members is not None:
            user += f"\n\nAvailable Cube members (use ONLY these):\n{json.dumps(members)}"
        if prev_error:
            user += (
                "\n\nYour previous attempt failed validation with:\n"
                f"{prev_error}\n"
                "Emit a corrected, complete view-spec that fixes these errors. Raw JSON only."
            )
        response = self._client_or_build().messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        from agents.tools.spec_generator import _text_of  # noqa: PLC0415 — reuse the parser seam

        return _text_of(response)
