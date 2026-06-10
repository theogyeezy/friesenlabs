"""AnthropicSpecGenerator — the real (Sonnet-backed) view-spec generator (TODO AI/P1).

Fills `build_view`'s model-call seam: given the user's ask + the tenant's real Cube members, a
Sonnet-tier model is prompted to emit a declarative view-spec (SPEC, NOT CODE — CLAUDE.md hard
constraint #7). The output is parsed defensively and validated against
`shared/schemas/view_spec.schema.json` PLUS the allowed-member catalog; on validation failure the
generator retries ONCE with the validator's errors fed back (reject-and-retry), then gives up.

Contract: `generate(request=..., allowed_members=...)` returns
    {"valid": bool, "spec": dict | None, "errors": list[str], "attempts": int}
— it never raises on model failure and never returns an unvalidated spec as `valid`.

Offline/import-safe: the `anthropic` SDK is imported lazily on first use (same seam as
`conv.synthesizer.AnthropicSynthesizer`); constructing the class needs no network and no creds.
Tests inject a fake `client`. With no client, no explicit key, and no ANTHROPIC_API_KEY in the
environment, `generate` degrades to a clear "generator unconfigured" result instead of raising —
the deployed API without AI creds must keep booting (draft-gate posture).

Note on the beta header: this is a plain `/v1/messages` call, so the Managed Agents beta header
is NOT required — the SDK applies it automatically to the `client.beta.*` namespaces only (same
rationale as `conv/synthesizer.py`; verified against the claude-api skill / SDK docs, 2026-06).
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterable

from agents.roster import SONNET
from shared import view_spec
from shared.config import ENV_ANTHROPIC_API_KEY

# One generation + one reject-and-retry with the validator errors fed back.
MAX_ATTEMPTS = 2

UNCONFIGURED_ERROR = (
    "spec generator unconfigured: no Anthropic client or API key "
    f"(inject client= for tests, or set {ENV_ANTHROPIC_API_KEY})"
)

# Stable system prompt (schema first, deterministic serialization — cache-friendly). The volatile
# parts (request, members, validator feedback) go in the user message.
_SYSTEM = (
    "You generate dashboard view-specs for a multi-tenant CRM. A view-spec is DECLARATIVE DATA, "
    "never code: no React/JS/HTML, only catalog blocks (kpi / chart / table) bound to governed "
    "Cube members.\n\n"
    "Respond with ONLY a JSON object that validates against this JSON schema:\n\n"
    + json.dumps(view_spec.SCHEMA, indent=2, sort_keys=True)
    + "\n\nRules:\n"
    "- Reference ONLY Cube members from the provided 'Available Cube members' list "
    "(Cube.field form); list every member you use in 'semantic_refs'.\n"
    "- Pick a short slug-like 'view_id' and a human-readable 'title' for the request.\n"
    "- Keep the layout minimal: only the blocks the request actually asks for.\n"
    "- Output raw JSON only — no prose, no markdown fences, no executable code of any kind."
)


def _text_of(response: Any) -> str:
    """Concatenate the text blocks of a Messages API response (tolerates dict-shaped fakes)."""
    parts: list[str] = []
    for block in getattr(response, "content", None) or []:
        if isinstance(block, dict):
            btype, text = block.get("type"), block.get("text")
        else:
            btype, text = getattr(block, "type", None), getattr(block, "text", None)
        if btype == "text" and isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _parse_spec(raw: str) -> dict | None:
    """Defensively parse the model's JSON view-spec. Returns None when unusable."""
    text = (raw or "").strip()
    if not text:
        return None
    # Tolerate markdown fences despite the prompt forbidding them.
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        # Last resort: the outermost {...} span (models sometimes wrap JSON in prose).
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except (ValueError, TypeError):
            return None
    return data if isinstance(data, dict) else None


class AnthropicSpecGenerator:
    """Sonnet-backed view-spec generator with schema+catalog validation and one retry.

    Lazy client: nothing touches the network at import or construction time. Inject `client`
    (any object with `.messages.create(...)`) in tests, or `api_key` for an explicit key —
    with neither, the SDK default credential resolution (ANTHROPIC_API_KEY env) applies on
    first use.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: Any = None,
        model: str = SONNET,
        max_tokens: int = 4096,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self._api_key = api_key
        self._client = client  # built lazily; tests inject a fake
        self.model = model
        self.max_tokens = max_tokens
        self.max_attempts = max(1, max_attempts)

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

    # ------------------------------------------------------------------ contract
    def generate(self, *, request: str, allowed_members: Iterable[str]) -> dict:
        """User ask + Cube catalog -> {"valid", "spec", "errors", "attempts"}.

        Validation (JSON schema + real-member check) runs on EVERY attempt; a failing attempt's
        errors are fed back to the model for the single retry. Never raises on model failure
        and never returns `valid: True` for a spec that did not pass `view_spec.validate`.
        """
        if not self.configured:
            return {"valid": False, "spec": None, "errors": [UNCONFIGURED_ERROR], "attempts": 0}

        members = sorted(set(allowed_members or []))
        errors: list[str] = []
        prev_error: str | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                raw = self._call_model(request, members, prev_error)
            except Exception as e:  # API error -> record, feed back, retry (never crash a turn)
                prev_error = f"model call failed: {e}"
                errors.append(prev_error)
                continue

            spec = _parse_spec(raw)
            if spec is None:
                prev_error = "model output was not a JSON object (emit the raw view-spec JSON only)"
                errors.append(prev_error)
                continue

            try:
                view_spec.validate(spec, allowed_members=set(members))
            except view_spec.ValidationError as e:
                prev_error = str(e)  # the validator's words go back to the model verbatim
                errors.append(prev_error)
                continue

            return {"valid": True, "spec": spec, "errors": errors, "attempts": attempt}

        # Reject-and-retry exhausted — never return unvalidated output as valid.
        return {"valid": False, "spec": None, "errors": errors, "attempts": self.max_attempts}

    # ------------------------------------------------------------------ model call
    def _call_model(self, request: str, members: list[str], prev_error: str | None) -> str:
        user = (
            f"User request:\n{request}\n\n"
            f"Available Cube members (use ONLY these):\n{json.dumps(members)}"
        )
        if prev_error:
            user += (
                "\n\nYour previous attempt failed validation with:\n"
                f"{prev_error}\n"
                "Emit a corrected view-spec that fixes these errors. Raw JSON only."
            )
        response = self._client_or_build().messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        return _text_of(response)
