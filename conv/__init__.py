"""conv — Phase 6, the conversational layer (Build Guide Steps 36–38).

The Moveworks-style front door: one NL entry point where a user asks, acts, and resolves in one
thread. This package is the *reusable logic* only — the HTTP front door (SSE, auth) is Phase 9.

Everything here is offline and injectable: no real Anthropic/AWS, no secrets. Importing any module
in `conv` (including `conv.session`) must not require network/cloud — the agent runtime is reached
through `agents.runtime.get_runtime` (FakeRuntime by default), and every client (cube, db/crm, rag,
the LLM disambiguator/synthesizer) is passed in.

Two genuinely new builds live here:
  - `slots.resolve_slots` — NL references → governed IDs/values, never silently guessing.
  - `rag.answer` — agentic RAG with citation assembly, never returning an uncited claim as grounded.
"""
from __future__ import annotations

from .analytics import Analytics, InMemoryAnalyticsStore
from .rag import Answer, Citation, answer
from .slots import Disambiguation, ResolvedSlots, SlotContext, resolve_slots

__all__ = [
    "Analytics",
    "InMemoryAnalyticsStore",
    "Answer",
    "Citation",
    "answer",
    "Disambiguation",
    "ResolvedSlots",
    "SlotContext",
    "resolve_slots",
]
