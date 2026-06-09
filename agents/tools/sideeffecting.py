"""Side-effecting tools (ALWAYS_ASK policy): they NEVER execute the side effect directly.

draft_email is AUTO (drafting is safe). send_email / update_deal / issue_quote are ALWAYS_ASK: invoking
them builds a proposal and routes it to Greenlight — the email is never sent, the CRM is never mutated,
until a human approves. The base class guarantees this; these classes only build the proposal payload.
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
    description = "Mutate a CRM deal (stage/amount). Requires human approval (Greenlight)."
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
