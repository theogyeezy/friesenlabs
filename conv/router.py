"""Tier-0 chat router — the Moveworks-style front door (2026-06-12).

One fast, deterministic classification decides the lane BEFORE any Managed-Agents session is
touched:

  * "knowledge"  the ask is corpus-shaped (policy/process/terms questions): answered DIRECTLY
                 by the server-side grounded RAG path (conv.rag.answer over the tenant-scoped
                 PgRagClient) in seconds — no coordinator, no delegation, no worker round-trips.
  * "crew"       everything else: actions (Greenlight-gated), research, CRM-state questions,
                 multi-step ops — the coordinator + specialists, minutes, async by design.

BIAS RULE: anything action-shaped or ambiguous goes to the CREW. The fast path must never
swallow a request that needs tools, drafts, or delegation — a slow correct answer beats a fast
wrong lane. The router is injected behind a tiny seam so an LLM classifier (Haiku) can replace
the heuristic later without touching conv.session.
"""
from __future__ import annotations

import re
from typing import Protocol

KNOWLEDGE = "knowledge"
CREW = "crew"


class Router(Protocol):
    def route(self, message: str) -> str: ...


# Action / research / delegation verbs — any hit means the CREW lane (tools, drafts, Greenlight).
_CREW_VERBS = re.compile(
    r"\b(send|email|update|move|change|issue|create|draft|schedule|book|call|text|"
    r"research|investigate|prepare|review|analyze|analyse|find out|reach out|follow.?up|"
    r"close|open|assign|remind|notify|have the team|tell the team)\b",
    re.I,
)

# CRM-state phrasing — questions about live tenant DATA (accounts/deals/pipeline state) need
# the crew's read tools, not the document corpus.
_CRM_STATE = re.compile(
    r"\b(how is|how's|what should|status of|doing\b|account\b|deal\b|deals\b|lead\b|leads\b|"
    r"pipeline\b|quota\b|forecast\b|this week|last week|today|"
    # Contact lookups are CRM data, not corpus (live miss, owner-reported 2026-06-12):
    # "what is X's phone number" must reach the crew's read_crm, not the knowledge lane.
    r"phone|mobile\b|cell\b|contact info|email address)\b",
    re.I,
)

# Corpus-shaped question openers/markers — policy/process/terms questions the knowledge base
# answers.
_KNOWLEDGE_SHAPE = re.compile(
    r"^(what|what's|whats|how|tell me|explain|describe|when|where|why|who|do we|does|is there|"
    r"are there|can i|can we)\b",
    re.I,
)


# Corpus NOUNS — strong knowledge signals that survive opener typos ("waht is our discont
# policy" misses the shape regex but plainly asks about a policy; owner-reported 2026-06-12).
# Checked AFTER the crew markers, so action/CRM asks mentioning these still go to the crew.
_CORPUS_NOUNS = re.compile(
    r"\b(polic(y|ies)|terms|pricing|price list|playbook|onboarding|warranty|rate card|"
    r"discount\w*|discont\w*|procedure|process for|faq)\b",
    re.I,
)


class HeuristicRouter:
    """Deterministic, offline v1. Crew-biased: verbs and CRM-state markers win over shape."""

    def route(self, message: str) -> str:
        text = (message or "").strip()
        if not text:
            return CREW
        if _CREW_VERBS.search(text):
            return CREW
        if _CRM_STATE.search(text):
            return CREW
        if _KNOWLEDGE_SHAPE.match(text) or text.endswith("?") or _CORPUS_NOUNS.search(text):
            return KNOWLEDGE
        return CREW
