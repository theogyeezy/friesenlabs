"""Side-effecting tools (ALWAYS_ASK policy): they NEVER execute the side effect directly.

draft_email is AUTO (drafting is safe). send_email / CRM writes / issue_quote are ALWAYS_ASK:
invoking them builds a proposal and routes it to Greenlight — email is never sent, the CRM is never
mutated, until a human approves. The base class guarantees this; these classes only build the
proposal payload.
"""
from __future__ import annotations

from .base import Policy, Tool, ToolContext


class DraftEmail(Tool):
    name = "draft_email"
    description = "Draft an outreach/follow-up email (no send)."
    input_schema = {
        "type": "object",
        "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "goal": {"type": "string"}},
        "required": ["to", "goal"],
    }
    policy = Policy.AUTO  # drafting is safe

    def _execute(self, ctx: ToolContext, *, to: str, goal: str, subject: str = "") -> dict:
        body = f"(draft) Re: {goal}"  # real impl asks the model; draft only, never sent
        return {"to": to, "subject": subject or goal, "body": body}


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
