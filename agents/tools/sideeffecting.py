"""Side-effecting tools (ALWAYS_ASK policy): they NEVER execute the side effect directly.

send_email / draft_email / CRM writes / issue_quote are ALWAYS_ASK: invoking them builds a proposal
and routes it to Greenlight — email is never sent, the CRM is never mutated, until a human approves.
The base class guarantees this; these classes only build the proposal payload.

draft_email is the agent-facing affordance the drafting specialists (nadia/echo) hold: the model
"drafts" an email and it STAGES as the canonical `send_email` approval (proposal_action), so the
queue, compliance class, applier, and UI are all the one send_email path. The body stays
model-authored (audit P0-1); the tool only guarantees CAN-SPAM compliance by construction.
"""
from __future__ import annotations

from .base import Policy, Tool, ToolContext

# A standard, compliant opt-out appended when the model's body lacks an unsubscribe mechanism, so a
# staged email always clears the deterministic CAN-SPAM floor (api/control/compliance.py) and is
# approvable — never stored `denied`, which would dead-end the very request the user asked to queue.
# Matches the product's established staged-email convention (scripts/seed_demo_tenant.py).
_OPT_OUT_FOOTER = "Reply \"unsubscribe\" and we'll stop sending these right away."


def _ensure_opt_out(body: str) -> str:
    """Return `body` unchanged when it already carries an unsubscribe mechanism (case-insensitive),
    else append the standard opt-out footer. The model's words are always preserved verbatim — the
    footer is a system-level compliance addendum, never a rewrite of the draft."""
    if "unsubscribe" in (body or "").lower():
        return body
    sep = "" if body.endswith("\n") else "\n\n"
    return f"{body}{sep}{_OPT_OUT_FOOTER}"


class DraftEmail(Tool):
    name = "draft_email"
    description = (
        "Draft an email and stage it for human approval in the Greenlight queue (it is NEVER sent "
        "automatically — a person reviews, edits, and approves it before it goes out). YOU author "
        "the complete, personalized body and pass it in `body`; this tool stages it verbatim and "
        "never writes content for you. Use this whenever the user wants an email drafted, queued, "
        "or sent."
    )
    channel = "email"  # so the compliance floor applies CAN-SPAM to the staged draft
    # Agent-facing name is draft_email; the queued action is the canonical, gated send_email.
    proposal_action = "send_email"
    input_schema = {
        "type": "object",
        "properties": {
            "to": {"type": "string"},
            "subject": {"type": "string"},
            "body": {
                "type": "string",
                "description": "The FULL email body, written by you — greeting to sign-off.",
            },
            "goal": {"type": "string", "description": "What this email is trying to achieve."},
        },
        "required": ["to", "body"],
    }
    policy = Policy.ALWAYS_ASK  # staging an email is a customer-facing action → human approval

    def _execute(self, ctx: ToolContext, *, to: str, body: str, subject: str = "",
                 goal: str = "") -> dict:
        # The body is MODEL-AUTHORED (the calling agent IS the model) and carried verbatim into the
        # proposal — never a server-side placeholder (audit P0-1), never a nested model call (the
        # worker carries no Anthropic key by design; shared/config.py key posture). The opt-out
        # footer is appended only when missing, so the staged email is CAN-SPAM compliant by
        # construction. Build the canonical send_email PROPOSAL only — the base class routes it to
        # Greenlight and the real send never runs until approval (record_only applier).
        return {
            "action": "send_email",
            "reasoning": f"Send email to {to}" + (f": {goal}" if goal else ""),
            "value_at_stake": None,
            "to": to,
            "subject": subject or goal or "Follow-up",
            "body": _ensure_opt_out(body),
        }


class SendEmail(Tool):
    name = "send_email"
    channel = "email"  # so the compliance validator applies CAN-SPAM
    description = "Send an email. Requires human approval (Greenlight)."
    input_schema = {
        "type": "object",
        "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
        "required": ["to", "body"],
    }
    policy = Policy.ALWAYS_ASK

    def _execute(self, ctx: ToolContext, *, to: str, body: str, subject: str = "") -> dict:
        # Build the PROPOSAL only — do not send. The base class routes this to Greenlight.
        return {
            "action": "send_email",
            "reasoning": f"Send email to {to}",
            "value_at_stake": None,
            "to": to,
            "subject": subject,
            "body": body,
        }


class UpdateDeal(Tool):
    name = "update_deal"
    description = "Mutate a CRM deal (stage/amount/name). Requires human approval (Greenlight)."
    input_schema = {
        "type": "object",
        "properties": {"deal_id": {"type": "string"}, "changes": {"type": "object"}},
        "required": ["deal_id", "changes"],
    }
    policy = Policy.ALWAYS_ASK

    def _execute(self, ctx: ToolContext, *, deal_id: str, changes: dict) -> dict:
        return {
            "action": "update_deal",
            "reasoning": f"Update deal {deal_id}: {changes}",
            "value_at_stake": changes.get("amount"),
            "deal_id": deal_id,
            "changes": changes,
        }


class UpdateContact(Tool):
    name = "update_contact"
    description = "Mutate a CRM contact (name/email/phone/title). Requires human approval (Greenlight)."
    input_schema = {
        "type": "object",
        "properties": {"contact_id": {"type": "string"}, "changes": {"type": "object"}},
        "required": ["contact_id", "changes"],
    }
    policy = Policy.ALWAYS_ASK

    def _execute(self, ctx: ToolContext, *, contact_id: str, changes: dict) -> dict:
        return {
            "action": "update_contact",
            "reasoning": f"Update contact {contact_id}: {changes}",
            "value_at_stake": None,
            "contact_id": contact_id,
            "changes": changes,
        }


class CreateActivity(Tool):
    name = "create_activity"
    description = "Create a CRM activity/note. Requires human approval (Greenlight)."
    input_schema = {
        "type": "object",
        "properties": {
            "contact_id": {"type": "string"},
            "deal_id": {"type": "string"},
            "kind": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["kind", "body"],
    }
    policy = Policy.ALWAYS_ASK

    def _execute(self, ctx: ToolContext, *, kind: str, body: str,
                 contact_id: str | None = None, deal_id: str | None = None) -> dict:
        target = contact_id or deal_id or "unlinked activity"
        return {
            "action": "create_activity",
            "reasoning": f"Create {kind} activity for {target}",
            "value_at_stake": None,
            "contact_id": contact_id,
            "deal_id": deal_id,
            "kind": kind,
            "body": body,
        }


class CreateDeal(Tool):
    name = "create_deal"
    description = "Create a CRM deal. Requires human approval (Greenlight)."
    input_schema = {
        "type": "object",
        "properties": {
            "company_id": {"type": "string"},
            "name": {"type": "string"},
            "stage": {"type": "string"},
            "amount": {"type": "number"},
        },
        "required": ["company_id", "name", "stage", "amount"],
    }
    policy = Policy.ALWAYS_ASK

    def _execute(self, ctx: ToolContext, *, company_id: str, name: str,
                 stage: str, amount: float | int | None = None) -> dict:
        return {
            "action": "create_deal",
            "reasoning": f"Create deal {name!r} in stage {stage!r}",
            "value_at_stake": amount,
            "company_id": company_id,
            "name": name,
            "stage": stage,
            "amount": amount,
        }


class IssueQuote(Tool):
    name = "issue_quote"
    description = "Issue a quote to a customer. Requires human approval (Greenlight)."
    input_schema = {
        "type": "object",
        "properties": {"deal_id": {"type": "string"}, "amount": {"type": "number"}},
        "required": ["deal_id", "amount"],
    }
    policy = Policy.ALWAYS_ASK

    def _execute(self, ctx: ToolContext, *, deal_id: str, amount: float) -> dict:
        return {
            "action": "issue_quote",
            "reasoning": f"Issue quote of {amount} on deal {deal_id}",
            "value_at_stake": amount,
            "deal_id": deal_id,
            "amount": amount,
        }
