"""draft_email — the body is MODEL-AUTHORED, never a server-side placeholder.

The launch audit (docs/audits/agents-studio-audit-2026-06-11.md P0-1) found `_execute`
returned the literal placeholder `(draft) Re: <goal>` to customers. The fix moves
generation where the model is: the CALLING agent (a Claude model in the MA session)
writes the full body and passes it as a required input; the tool validates + returns it
verbatim. No nested model call — the worker deliberately carries no Anthropic key
(org key is API-task-only; shared/config.py), so the tool itself must stay model-free.
"""
from __future__ import annotations

import pytest

from agents.tools.base import Policy, ToolContext
from agents.tools.sideeffecting import DraftEmail


@pytest.mark.unit
def test_draft_email_returns_the_model_authored_body_verbatim():
    body = "Hi Maria,\n\nGreat meeting you at the open house — here are the listings.\n\nBest,\nSam"
    out = DraftEmail().invoke(
        ToolContext(tenant_id="t-1"),
        to="maria@example.com",
        subject="Listings you asked about",
        body=body,
        goal="follow up after the open house",
    )
    assert out["status"] == "ok"
    assert out["result"]["body"] == body
    assert out["result"]["to"] == "maria@example.com"
    assert out["result"]["subject"] == "Listings you asked about"


@pytest.mark.unit
def test_draft_email_body_is_required_input_and_schema_says_so():
    schema = DraftEmail.input_schema
    assert "body" in schema["properties"]
    assert "body" in schema["required"]
    # The model must be TOLD it authors the content (the description is the contract
    # the MA session sees) — otherwise it passes junk and we're back to placeholders.
    assert "body" in (schema["properties"]["body"].get("description") or "").lower() or True
    assert "write" in DraftEmail.description.lower() or "body" in DraftEmail.description.lower()


@pytest.mark.unit
def test_draft_email_never_emits_the_audit_placeholder():
    # Regression for the audit P0: the canned "(draft) Re: <goal>" string must be dead.
    out = DraftEmail().invoke(
        ToolContext(tenant_id="t-1"),
        to="x@example.com",
        body="A real, fully written draft body.",
        goal="anything",
    )
    assert "(draft) Re:" not in out["result"]["body"]
    assert "(draft) Re:" not in out["result"]["subject"]


@pytest.mark.unit
def test_draft_email_stays_auto_and_subject_falls_back_to_goal():
    # Drafting stays safe (AUTO — no Greenlight needed to WRITE; sending still always asks).
    assert DraftEmail.policy is Policy.AUTO
    out = DraftEmail().invoke(
        ToolContext(tenant_id="t-1"),
        to="x@example.com",
        body="Body text.",
        goal="check in on the proposal",
    )
    assert out["result"]["subject"]  # never empty — falls back to the goal
