"""The conversation facade (Build Guide Step 38) — the seam the Phase 9 HTTP front door will call.

`Conversation` is a thin client over the agent runtime: one Managed Agents session per conversation.
It does NOT reimplement the agent plane, the tools, or the approval queue — it wires the Phase 6
pieces (slots, RAG+citations, analytics) onto the Phase 4 runtime/tools and the Phase 5 Greenlight,
all by injection.

Offline-safe: importing this module needs no AWS/Anthropic. The runtime defaults to FakeRuntime via
`agents.runtime.get_runtime`; every other client (rag, crm, cube, greenlight, synthesizer, analytics)
is injected. `today` is injected for deterministic date math.

A turn returns a structured result:
  {answer, citations, pending_approvals, slots, delegations, session_id, tenant_id}

Routing is intentionally simple and offline:
  - a *knowledge* question -> agentic RAG with citations (conv.rag.answer).
  - an *action* utterance (matched to a side-effecting Phase 4 tool) -> the tool is invoked, which —
    by the Phase 4 base-class guarantee — routes a proposal to Greenlight WITHOUT performing the side
    effect, surfacing a pending approval.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from agents.runtime import Session, get_runtime
from agents.tools.base import ToolContext
from agents.tools.sideeffecting import IssueQuote, SendEmail, UpdateDeal

from .analytics import Analytics, Event, EventType
from .rag import Answer, RagContext, answer as rag_answer
from .slots import SlotContext, resolve_slots

# Lightweight intent matching for the offline facade. The real front door lets the coordinator pick;
# here we map a few obvious action verbs to the corresponding Phase 4 side-effecting tool.
_ACTION_TOOLS = {
    "send_email": (SendEmail, re.compile(r"\b(send|email|reach out|follow up with)\b", re.I)),
    "update_deal": (UpdateDeal, re.compile(r"\b(update|move|change)\b.*\bdeal\b", re.I)),
    "issue_quote": (IssueQuote, re.compile(r"\b(quote|issue a quote|pricing)\b", re.I)),
}


@dataclass
class Turn:
    answer: str
    citations: list[dict] = field(default_factory=list)
    pending_approvals: list[dict] = field(default_factory=list)
    slots: dict = field(default_factory=dict)
    needs_disambiguation: list[dict] = field(default_factory=list)
    delegations: list[str] = field(default_factory=list)
    session_id: str | None = None
    tenant_id: str | None = None

    def as_dict(self) -> dict:
        return {
            "answer": self.answer,
            "citations": self.citations,
            "pending_approvals": self.pending_approvals,
            "slots": self.slots,
            "needs_disambiguation": self.needs_disambiguation,
            "delegations": self.delegations,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
        }


class Conversation:
    """One MA session per conversation. Thin client; everything injected."""

    def __init__(
        self,
        *,
        tenant_id: str,
        today: date,
        runtime: Any = None,
        coordinator_id: str | None = None,
        vault_id: str | None = None,
        rag: Any = None,
        crm: Any = None,
        rag_crm: Any = None,
        cube: Any = None,
        synthesizer: Any = None,
        disambiguator: Any = None,
        greenlight: Any = None,
        analytics: Analytics | None = None,
        agent: str | None = "uplift-orchestrator",
    ) -> None:
        self.tenant_id = tenant_id
        self.today = today
        # Default to the offline FakeRuntime — import + construction never touch the network.
        self.runtime = runtime or get_runtime({"runtime": "fake"})
        self.rag = rag
        self.crm = crm            # tool-side CRM/db client: .read(entity=, limit=), .set_tenant(...)
        self.rag_crm = rag_crm    # RAG-side CRM client: .read(tenant_id=, query=)
        self.cube = cube
        self.synthesizer = synthesizer
        self.disambiguator = disambiguator
        self.greenlight = greenlight
        self.analytics = analytics
        self.agent = agent

        # If a coordinator id was not provided, register the standard roster on the runtime so the
        # FakeRuntime can simulate delegations. (Pure orchestration; no network on FakeRuntime.)
        if coordinator_id is None:
            from agents import coordinator as _coord  # local import keeps module import cheap

            coordinator_id = _coord.build(self.runtime)
        self.coordinator_id = coordinator_id

        self.session: Session = self.runtime.create_session(
            self.coordinator_id, tenant_id=tenant_id, vault_id=vault_id
        )

    # ------------------------------------------------------------------ helpers
    def _tool_ctx(self) -> ToolContext:
        return ToolContext(
            tenant_id=self.tenant_id,
            agent=self.agent,
            db=self.crm,
            cube=self.cube,
            rag=self.rag,
            greenlight=self.greenlight,
        )

    def _record(self, type: EventType, payload: dict) -> None:
        if self.analytics is not None:
            self.analytics.record(
                Event(tenant_id=self.tenant_id, type=type, session_id=self.session.id, payload=payload)
            )

    def _resolve_slots(self, text: str) -> tuple[dict, list[dict]]:
        sc = SlotContext(
            tenant_id=self.tenant_id,
            today=self.today,
            crm=self.crm,
            cube=self.cube,
            disambiguator=self.disambiguator,
        )
        rs = resolve_slots(text, sc)
        return rs.slots, [d.as_dict() for d in rs.ambiguous]

    def _match_action(self, text: str):
        for name, (tool_cls, pattern) in _ACTION_TOOLS.items():
            if pattern.search(text):
                return name, tool_cls
        return None, None

    # ------------------------------------------------------------------ public API
    def send(self, message: str, **action_kwargs: Any) -> Turn:
        """Forward one user message; return the structured turn.

        `action_kwargs` carries any explicit args for a matched side-effecting tool (e.g. to/body for
        send_email). In the real front door these come from slot resolution + the coordinator.
        """
        self._record(EventType.UTTERANCE, {"text": message})

        slots, ambiguous = self._resolve_slots(message)

        action_name, tool_cls = self._match_action(message)
        if tool_cls is not None:
            return self._handle_action(message, action_name, tool_cls, slots, ambiguous, action_kwargs)

        return self._handle_knowledge(message, slots, ambiguous)

    def _handle_action(self, message, action_name, tool_cls, slots, ambiguous, action_kwargs) -> Turn:
        ctx = self._tool_ctx()
        out = tool_cls().invoke(ctx, **action_kwargs)
        self._record(EventType.TOOL_CALL, {"tool": action_name, "status": out.get("status")})

        pending: list[dict] = []
        if out.get("status") == "pending_approval":
            approval = out.get("approval")
            if approval is not None:
                pending.append(approval)
                self._record(EventType.APPROVAL, {"approval_id": approval.get("id"), "action": action_name})
            else:
                # Greenlight unconfigured — still surface the proposal so nothing silently executes.
                pending.append({"status": "pending", "proposal": out.get("proposal")})

        # Let the (fake) coordinator also see the message so delegations are recorded for the trace.
        resp = self.runtime.send_message(self.session, message)
        return Turn(
            answer=out.get("proposal", {}).get("reasoning", "Prepared an action for your approval."),
            pending_approvals=pending,
            slots=slots,
            needs_disambiguation=ambiguous,
            delegations=resp.get("delegations", []),
            session_id=self.session.id,
            tenant_id=self.tenant_id,
        )

    def _handle_knowledge(self, message, slots, ambiguous) -> Turn:
        ans: Answer
        if self.rag is not None:
            rctx = RagContext(
                tenant_id=self.tenant_id,
                rag=self.rag,
                crm=self.rag_crm,
                synthesizer=self.synthesizer,
            )
            ans = rag_answer(message, rctx)
        else:
            ans = Answer(answer="I don't have grounded sources to answer that.", citations=[])

        resp = self.runtime.send_message(self.session, message)
        return Turn(
            answer=ans.answer,
            citations=[c.as_dict() for c in ans.citations],
            slots=slots,
            needs_disambiguation=ambiguous,
            delegations=resp.get("delegations", []),
            session_id=self.session.id,
            tenant_id=self.tenant_id,
        )
