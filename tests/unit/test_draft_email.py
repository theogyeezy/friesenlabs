"""draft_email STAGES an email into the Greenlight approval queue — it never composes-and-drops.

The live matrix gap (2026-06-12): the email specialists (nadia/echo) held only `draft_email`,
which was `Policy.AUTO` — invoking it returned a draft object and queued NOTHING, so a user who
asked to "queue this for my approval" got an empty `/approvals`. draft_email is now the
agent-facing affordance that produces a `send_email`-shaped Greenlight proposal: drafting an email
IS staging it for a human (review / edit / approve), and the side effect (a real send) never runs
until approval (record_only applier, draft-only held).

Two invariants this file pins:
  * the body stays MODEL-AUTHORED and is carried verbatim into the proposal (the audit P0-1 fix);
  * the staged email is CAN-SPAM compliant BY CONSTRUCTION — the tool appends a standard opt-out
    footer when the model's body lacks one, so the proposal is always approvable (never stored
    `denied` for a missing unsubscribe, which would be a worse dead-end than the old gap).
"""
from __future__ import annotations

import pytest

from agents.tools.base import InMemoryGreenlight, Policy, ToolContext
from agents.tools.sideeffecting import DraftEmail
from api.control.compliance import validate
from api.control.types import Action


@pytest.mark.unit
def test_draft_email_is_side_effecting_and_stages_a_send_email_proposal():
    # The policy/channel are the server-side truth the compliance floor + worker key off.
    assert DraftEmail.policy is Policy.ALWAYS_ASK
    assert DraftEmail.channel == "email"
    # The agent-facing tool name stays `draft_email` (the verb the model reaches for), but it
    # STAGES the canonical gated `send_email` action — same applier, compliance, UI.
    assert DraftEmail.proposal_action == "send_email"


@pytest.mark.unit
def test_draft_email_routes_to_greenlight_and_carries_the_model_body_verbatim():
    gl = InMemoryGreenlight()
    body = ("Hi Maria,\n\nGreat meeting you at the open house — here are the listings.\n\n"
            "Best,\nSam\n\nReply \"unsubscribe\" to opt out.")
    out = DraftEmail().invoke(
        ToolContext(tenant_id="t-1", agent="nadia", greenlight=gl),
        to="maria@example.com",
        subject="Listings you asked about",
        body=body,
        goal="follow up after the open house",
    )
    # Staged, not executed: the worker/UI digest keys off this status.
    assert out["status"] == "pending_approval"
    proposal = out["proposal"]
    assert proposal["action"] == "send_email"
    assert proposal["to"] == "maria@example.com"
    assert proposal["subject"] == "Listings you asked about"
    # Model-authored body carried verbatim (already compliant → untouched).
    assert proposal["body"] == body
    # And it actually landed in the queue.
    assert len(gl.queue) == 1
    assert gl.queue[0]["action"] == "send_email"


@pytest.mark.unit
def test_draft_email_appends_a_compliant_opt_out_when_the_model_omits_one():
    gl = InMemoryGreenlight()
    body = "Hi Vada,\n\nFollowing up on the RTU replacement proposal.\n\nBest,\nMatt"
    out = DraftEmail().invoke(
        ToolContext(tenant_id="t-1", agent="echo", greenlight=gl),
        to="vada@example.com",
        subject="Following up",
        body=body,
    )
    staged = out["proposal"]["body"]
    # The model's words are preserved AND a real unsubscribe mechanism is now present.
    assert body in staged
    assert "unsubscribe" in staged.lower()
    # The staged email PASSES the deterministic CAN-SPAM floor (so it is approvable, not denied).
    verdict = validate(Action(
        name="send_email", tenant_id="t-1", side_effecting=True, channel="email",
        payload=out["proposal"],
    ))
    assert verdict.ok, verdict.reason


@pytest.mark.unit
def test_draft_email_does_not_double_append_when_body_already_has_unsubscribe():
    gl = InMemoryGreenlight()
    body = "Hi — details attached. To opt out, reply UNSUBSCRIBE."
    out = DraftEmail().invoke(
        ToolContext(tenant_id="t-1", agent="nadia", greenlight=gl),
        to="x@y.com", subject="s", body=body,
    )
    # Already compliant (case-insensitive) → verbatim, no footer appended.
    assert out["proposal"]["body"] == body


@pytest.mark.unit
def test_draft_email_body_is_required_input_and_the_description_says_the_model_authors_it():
    schema = DraftEmail.input_schema
    assert "body" in schema["properties"]
    assert "body" in schema["required"]
    # The model must be TOLD it authors the content AND that this stages for approval.
    desc = DraftEmail.description.lower()
    assert "you author" in desc or "you write" in desc
    assert "approval" in desc or "greenlight" in desc


@pytest.mark.unit
def test_draft_email_never_emits_the_audit_placeholder():
    # Regression for the audit P0: the canned "(draft) Re: <goal>" string must be dead.
    out = DraftEmail().invoke(
        ToolContext(tenant_id="t-1", greenlight=InMemoryGreenlight()),
        to="x@example.com",
        body="A real, fully written draft body. Reply unsubscribe to opt out.",
        goal="anything",
    )
    assert "(draft) Re:" not in out["proposal"]["body"]
    assert "(draft) Re:" not in out["proposal"]["subject"]


@pytest.mark.unit
def test_draft_email_without_a_greenlight_is_pending_not_executed():
    # No queue wired (the worker boots without a DSN): still NEVER a silent send — surfaces
    # pending_approval/unconfigured, never status ok.
    out = DraftEmail().invoke(
        ToolContext(tenant_id="t-1"), to="x@y.com", subject="s", body="hi unsubscribe",
    )
    assert out["status"] == "pending_approval"
    assert out.get("greenlight") == "unconfigured"


@pytest.mark.unit
def test_draft_email_lands_pending_through_the_real_greenlight_and_registry_resolver():
    # The TRUE end-to-end: draft_email -> base.invoke(action=send_email) -> the production
    # Greenlight (default registry channel resolver classifies send_email=email) -> the CAN-SPAM
    # floor passes (footer guaranteed) -> a PENDING approval the human can act on. This is exactly
    # the worker path; the model omitting an opt-out must NOT dead-end as `denied`.
    from api.control.greenlight import Greenlight, InMemoryApprovalStore

    gl = Greenlight(store=InMemoryApprovalStore())  # default resolver = trusted registry
    out = DraftEmail().invoke(
        ToolContext(tenant_id="t-9", agent="echo", greenlight=gl),
        to="vada@example.com", subject="Following up",
        body="Hi Vada,\n\nFollowing up on the proposal.\n\nMatt",  # NO unsubscribe authored
    )
    rec = out["approval"]
    assert rec["status"] == "pending", rec  # approvable — never denied for missing unsubscribe
    assert rec["proposed_action"]["action"] == "send_email"
    assert "unsubscribe" in rec["proposed_action"]["body"].lower()
    assert gl.count_pending("t-9") == 1


@pytest.mark.unit
def test_draft_email_approves_to_record_only_never_sends():
    # The full draft-only guarantee for the NEW path: stage -> human approves -> the applier is
    # record_only (performed=False, no real send), so an approved drafted email can never read as
    # "sent". There is deliberately NO `draft_email` applier — the proposal action is send_email.
    from api.control.appliers import APPLIERS, apply_approved_action, was_performed
    from api.control.greenlight import Greenlight, InMemoryApprovalStore

    assert "draft_email" not in APPLIERS  # the staged action dispatches as send_email
    gl = Greenlight(store=InMemoryApprovalStore())
    out = DraftEmail().invoke(
        ToolContext(tenant_id="t-7", agent="nadia", greenlight=gl),
        to="vada@example.com", subject="Renewal", body="Hi Vada — renewal follow-up.",
    )
    approved = gl.decide("t-7", out["approval"]["id"], "approve", decided_by="user-1")
    assert approved["status"] == "approved"
    result = apply_approved_action(None, "t-7", approved["proposed_action"])
    assert was_performed(result) is False  # record-only — the real send never ran
    assert result["performed"] is False


@pytest.mark.unit
def test_opt_out_footer_handles_empty_and_missing_bodies():
    # Defensive: the compliance floor must never be skippable on a malformed/empty body. A blank
    # body still yields a body that contains a real unsubscribe mechanism.
    from agents.tools.sideeffecting import _ensure_opt_out

    for raw in ("", None):
        out = _ensure_opt_out(raw)  # type: ignore[arg-type]
        assert "unsubscribe" in out.lower()
