"""Unit: the Tier-0 chat router (the Moveworks-style front door, 2026-06-12).

One fast classification decides the lane BEFORE any Managed-Agents session is touched:
  * "knowledge" -> answered directly by the server-side grounded RAG path (seconds);
  * "crew"      -> the coordinator + specialists (minutes, async by design).
The default HeuristicRouter is deterministic and offline; an LLM router can be injected
behind the same seam later. Bias: anything action-shaped or ambiguous goes to the CREW —
the fast path must never swallow a request that needs tools, drafts, or delegation.
"""
import pytest

from conv.router import HeuristicRouter


@pytest.mark.unit
@pytest.mark.parametrize("message", [
    "What is our discount policy?",
    "How long does onboarding take for a new customer?",
    "what's the loyalty discount rule",
    "Tell me about our payment terms",
    "Explain the renewal playbook",
])
def test_knowledge_shaped_questions_take_the_fast_lane(message):
    assert HeuristicRouter().route(message) == "knowledge"


@pytest.mark.unit
@pytest.mark.parametrize("message", [
    "Send a follow-up email to the Acme lead",
    "Update the Westlake deal to negotiation",
    "Issue a quote for Meridian",
    "Research this prospect and prepare an outreach plan",
    "review my pipeline and draft follow-ups for stale deals",
    "have the team investigate why renewals dipped",
    "How is the Acme account doing?",          # CRM-state ask -> crew (reads live data)
    "what should we do about the Acme lead?",  # open-ended ops -> crew
    # LIVE MISS (2026-06-12, owner-reported): a contact lookup is CRM data, not corpus —
    # the fast lane refused honestly but the crew could have answered from read_crm.
    "what is Vada Fenwick phone number",
    "what's the phone number for Vada Fenwick?",
    "contact info for the Westlake facilities manager",
    "what is Jane Doe's mobile?",
])
def test_action_research_and_crm_asks_go_to_the_crew(message):
    assert HeuristicRouter().route(message) == "crew"


@pytest.mark.unit
def test_ambiguity_defaults_to_the_crew():
    assert HeuristicRouter().route("") == "crew"
    assert HeuristicRouter().route("hmm") == "crew"
