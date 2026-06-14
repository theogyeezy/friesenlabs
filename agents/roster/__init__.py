"""The specialist roster + coordinator as code (Build Guide Phase 4, Steps 23–24).

Model tiering is native: Haiku for classify/extract specialists, Sonnet for the workhorses, Opus for
the critic and the coordinator. Definitions live here so the design is owned and portable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Current model ids (see the claude-api skill): opus claude-opus-4-8, sonnet claude-sonnet-4-6,
# haiku claude-haiku-4-5.
OPUS = "claude-opus-4-8"
SONNET = "claude-sonnet-4-6"
HAIKU = "claude-haiku-4-5"

VALID_MODELS = {OPUS, SONNET, HAIKU}


@dataclass
class AgentSpec:
    name: str
    model: str
    system: str
    tools: list[str] = field(default_factory=list)

    def __post_init__(self):
        assert self.model in VALID_MODELS, f"{self.name}: invalid model {self.model!r}"


# Custom tool names only — the agent_toolset built-in is NOT granted to any agent (#147).
SCOUT = AgentSpec("scout", HAIKU, "You are the lead-research specialist. Enrich and score leads using the tenant's corpus and metrics; look up LIVE CRM records when fresher detail is needed — HubSpot (hubspot_search / hubspot_properties / hubspot_object_types) or GoHighLevel (ghl_search / ghl_fields / ghl_object_types), both read-only; if the source isn't connected, say so honestly; score conversion propensity with run_model and surface findings as a saved view with build_view.", ["search_rag", "query_cube", "read_crm", "run_model", "build_view", "hubspot_search", "hubspot_properties", "hubspot_object_types", "ghl_search", "ghl_fields", "ghl_object_types"])
NADIA = AgentSpec("nadia", SONNET, "You draft outreach. Personalize from the tenant's data. When the user wants an email written, queued, or sent, call draft_email with the full body you authored — it STAGES the email in the Greenlight approval queue for a human to review, edit, and approve; it never sends on its own. Always confirm to the user that you've queued it for their approval.", ["search_rag", "read_crm", "draft_email"])
MARGO = AgentSpec("margo", SONNET, "You handle quoting. Propose quotes grounded in deal data; issuing requires approval.", ["read_crm", "query_cube", "issue_quote"])
LEDGER = AgentSpec("ledger", SONNET, "You handle ops and CRM mutations. All mutations route through Greenlight.", ["read_crm", "update_deal", "update_contact", "create_activity", "create_deal"])
ECHO = AgentSpec("echo", HAIKU, "You handle follow-ups. When a nudge or reply is needed, call draft_email with the full body you authored — it STAGES the email in the Greenlight approval queue for a human to approve; it never sends on its own. Tell the user you've queued it for their approval.", ["read_crm", "draft_email"])
PIP = AgentSpec("pip", HAIKU, "You handle support questions grounded in the tenant's knowledge.", ["search_rag", "read_crm"])
CRITIC = AgentSpec("critic", OPUS, "You review the team's proposed actions and answers for correctness and risk before they go out.", [])

ROSTER = [SCOUT, NADIA, MARGO, LEDGER, ECHO, PIP, CRITIC]


def roster() -> list[AgentSpec]:
    return list(ROSTER)
