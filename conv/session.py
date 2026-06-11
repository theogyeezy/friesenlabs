"""The conversation facade (Build Guide Step 38) — the seam the Phase 9 HTTP front door will call.

`Conversation` is a thin client over the agent runtime: one Managed Agents session per conversation.
It does NOT reimplement the agent plane, the tools, or the approval queue — it wires the Phase 6
pieces (slots, RAG+citations, analytics) onto the Phase 4 runtime/tools and the Phase 5 Greenlight,
all by injection.

Offline-safe: importing this module needs no AWS/Anthropic. The runtime defaults to FakeRuntime via
`agents.runtime.get_runtime`; every other client (rag, crm, cube, greenlight, synthesizer, analytics)
is injected. `today` is injected for deterministic date math — a plain `date` for fixed-clock tests,
or a zero-arg callable resolved FRESH PER TURN (the prod /chat path: a Conversation is cached across
requests per tenant, so a frozen construction-time date would silently rot date math).

A turn returns a structured result:
  {answer, citations, pending_approvals, slots, delegations, session_id, tenant_id}

Routing (TODO AI/P1 resolved — coordinator-driven on real runtimes):
  - FakeRuntime ONLY (explicitly gated): the offline regex facade —
      * a *knowledge* question -> agentic RAG with citations (conv.rag.answer).
      * an *action* utterance (matched to a side-effecting Phase 4 tool) -> the tool is invoked,
        which — by the Phase 4 base-class guarantee — routes a proposal to Greenlight WITHOUT
        performing the side effect, surfacing a pending approval.
  - Any real runtime: tool selection comes from the COORDINATOR — the `send_message` event
    digest carries the answer, delegations, served tool calls and surfaced pending events.
    THE CONV LAYER EXECUTES NO REGISTRY TOOLS (docs/decisions/custom-tool-execution-path.md:
    ONE executor owns tool execution). On ManagedAgentsRuntime that executor is the deployed
    EnvironmentWorker (read-only tools auto-run SERVER-SIDE in the VPC; side-effecting tools
    land a Greenlight proposal via the Phase 4 base class IN THE WORKER, draft-only). On the
    HIPAA fallback (SelfHostedToolUseRuntime) the runtime's own tool-use loop is that executor,
    fed by the tenant-scoped ToolContext builder this Conversation binds onto its
    `tool_context_factory` seam. Either way the digest is handled identically here:
      * `tool_results` (served calls) are recorded to analytics — never re-run;
      * already-routed `tool_name` entries (a gated call's Greenlight proposal) pass through
        untouched — the proposal is never enqueued twice; their approvals hit analytics;
      * anything that reaches `pending_approvals` UN-served (worker down, unknown tool) is
        surfaced untouched — an unknown name is never default-allowed and NOTHING is resolved
        through the registry or invoked in this process.
    Knowledge-shaped turns (nothing queued for approval) additionally run the SAME
    grounded-citation RAG path as the facade (`_grounded_answer`), so live chat answers carry
    citations whose source_refs exist in the tenant-scoped retrieved set — the citation
    invariant holds on BOTH runtimes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from agents.runtime import FakeRuntime, Session, get_runtime
from agents.tools.base import ToolContext
from agents.tools.sideeffecting import IssueQuote, SendEmail, UpdateDeal

from .analytics import Analytics, Event, EventType
from .rag import Answer, RagContext, answer as rag_answer
from .slots import SlotContext, resolve_slots
from .views import BALTO_STATUS, detect_view_intent

# Sentinel distinguishing "runtime has no tool_context_factory seam" from "seam present, unset".
_SEAM_ABSENT = object()

# Lightweight intent matching for the offline facade — FakeRuntime ONLY (explicitly gated in
# send()). On a real runtime the coordinator picks the tools; this regex never runs there.
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
    # Balto (conv/views.py): True when the utterance asks to SEE data (a view/graph/chart) —
    # the answer is then the EXACT Balto status line and `view_request` echoes the ask the
    # client forwards to POST /views/synthesize. Nothing is generated inside the chat turn.
    view_intent: bool = False
    view_request: str | None = None

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
            "view_intent": self.view_intent,
            "view_request": self.view_request,
        }


class Conversation:
    """One MA session per conversation. Thin client; everything injected.

    Per-tenant provisioning is HOISTED OUT of the request path: `coordinator_id` /
    `environment_id` are the tenant's persisted ids, resolved by the caller from a
    `agents.workspace_store.WorkspaceStore` row (written once at provisioning) — a Conversation
    never rebuilds the roster per request. The only exception is a clearly-gated test/dev
    fallback: with no `coordinator_id` AND a FakeRuntime, the standard roster is registered
    in-memory so the offline facade keeps simulating delegations. On any real runtime a missing
    coordinator_id raises — the tenant simply is not provisioned.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        today: date | Callable[[], date],
        runtime: Any = None,
        coordinator_id: str | None = None,
        environment_id: str | None = None,
        vault_id: str | None = None,
        rag: Any = None,
        crm: Any = None,
        rag_crm: Any = None,
        cube: Any = None,
        cortex: Any = None,
        synthesizer: Any = None,
        spec_generator: Any = None,
        disambiguator: Any = None,
        greenlight: Any = None,
        analytics: Analytics | None = None,
        agent: str | None = "uplift-orchestrator",
    ) -> None:
        self.tenant_id = tenant_id
        # `today` may be a fixed date (deterministic tests) or a zero-arg callable resolved
        # FRESH PER ACCESS (prod: `date.today` — a cached Conversation outlives the day it was
        # built on, so date math must never freeze at construction time).
        self._today = today
        # Default to the offline FakeRuntime — import + construction never touch the network.
        self.runtime = runtime or get_runtime({"runtime": "fake"})
        self.rag = rag
        self.crm = crm            # tool-side CRM/db client: .read(entity=, limit=), .set_tenant(...)
        self.rag_crm = rag_crm    # RAG-side CRM client: .read(tenant_id=, query=)
        self.cube = cube
        self.cortex = cortex      # persistent per-tenant model registry (ml.registry) -> run_model
        self.synthesizer = synthesizer
        # Default view-spec generator for build_view (ctx.extra['generate_spec']); None preserves
        # build_view's explicit raise — a missing generator is a programming error, not a mode.
        self.spec_generator = spec_generator
        self.disambiguator = disambiguator
        self.greenlight = greenlight
        self.analytics = analytics
        self.agent = agent

        # TEST/DEV FALLBACK ONLY (clearly gated): with no persisted coordinator id, register the
        # standard roster in-memory so the offline facade can simulate delegations. This is the
        # per-request provisioning the WorkspaceStore exists to avoid — it is allowed ONLY on
        # FakeRuntime; a real runtime without a coordinator_id means the tenant isn't provisioned.
        if coordinator_id is None:
            if not isinstance(self.runtime, FakeRuntime):
                raise RuntimeError(
                    "coordinator_id is required on a non-fake runtime — resolve the tenant's "
                    "persisted id from the WorkspaceStore (is this tenant provisioned?); "
                    "rebuilding the roster per request is not allowed"
                )
            from agents import coordinator as _coord  # noqa: PLC0415

            coordinator_id = _coord.build(self.runtime)
        self.coordinator_id = coordinator_id
        self.environment_id = environment_id

        # The session binds THIS tenant's persisted environment (per-tenant, never instance-global).
        self.session: Session = self.runtime.create_session(
            self.coordinator_id, tenant_id=tenant_id, vault_id=vault_id,
            environment_id=environment_id,
        )

        # THE HIPAA-FALLBACK EXECUTION SEAM ONLY: SelfHostedToolUseRuntime (a direct Messages
        # tool-use loop — no Managed Agents, no worker) carries a `tool_context_factory` seam and
        # IS its own single tool executor; when the caller injected nothing, bind this
        # conversation's tenant-scoped context builder so that loop can run tenant-bound. A
        # factory injected at runtime construction always wins. ManagedAgentsRuntime does NOT
        # carry the seam (the deployed EnvironmentWorker is the single executor there —
        # docs/decisions/custom-tool-execution-path.md), and FakeRuntime/stubs don't either —
        # nothing binds for them.
        if getattr(self.runtime, "tool_context_factory", _SEAM_ABSENT) is None:
            self.runtime.tool_context_factory = self._session_tool_ctx

    # ------------------------------------------------------------------ helpers
    @property
    def today(self) -> date:
        """The CURRENT date for this turn: a callable provider is resolved fresh per access
        (frozen-'today' fix — cached Conversations outlive their construction day); a plain
        date passes through unchanged (deterministic tests)."""
        provider = self._today
        return provider() if callable(provider) else provider

    def _session_tool_ctx(self, session: Session) -> ToolContext:
        """ToolContext for the self-hosted (HIPAA-fallback) runtime's tool-use loop. THE TRUST
        RULE: the tenant comes from the SESSION metadata only (set from the verified claim at
        create_session) — never re-read from conversation/request state. Fresh extra dict per
        call — tool invocations must never share mutable context state."""
        extra: dict = {}
        if self.spec_generator is not None:
            extra["generate_spec"] = self.spec_generator
        return ToolContext(
            tenant_id=session.metadata["tenant_id"],
            agent=self.agent,
            db=self.crm,
            cube=self.cube,
            rag=self.rag,
            cortex=self.cortex,
            greenlight=self.greenlight,
            extra=extra,
        )

    def _tool_ctx(self) -> ToolContext:
        # Fresh extra dict per call — tool invocations must never share mutable context state.
        extra: dict = {}
        if self.spec_generator is not None:
            extra["generate_spec"] = self.spec_generator
        return ToolContext(
            tenant_id=self.tenant_id,
            agent=self.agent,
            db=self.crm,
            cube=self.cube,
            rag=self.rag,
            cortex=self.cortex,
            greenlight=self.greenlight,
            extra=extra,
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

    def _grounded_answer(self, message: str) -> Answer:
        """The citation-invariant agentic-RAG path (conv.rag.answer) — ONE implementation shared
        by the offline facade (_handle_knowledge) and the live coordinator path
        (_handle_coordinator). Retrieval is tenant-scoped (PgRagClient under RLS in prod) and
        assembly guarantees every grounded citation's source_ref EXISTS in the retrieved set —
        an uncited claim is dropped, never returned as grounded."""
        rctx = RagContext(
            tenant_id=self.tenant_id,
            rag=self.rag,
            crm=self.rag_crm,
            synthesizer=self.synthesizer,
        )
        return rag_answer(message, rctx)

    # ------------------------------------------------------------------ public API
    def send(self, message: str, **action_kwargs: Any) -> Turn:
        """Forward one user message; return the structured turn.

        `action_kwargs` carries any explicit args for a side-effecting tool (e.g. to/body for
        send_email) — they top up the coordinator's tool input on the real path, and feed the
        regex-matched tool on the FakeRuntime facade.
        """
        self._record(EventType.UTTERANCE, {"text": message})

        slots, ambiguous = self._resolve_slots(message)

        # BALTO (conv/views.py): a view-shaped ask routes to the dedicated synthesis path on
        # EVERY runtime — the chat turn answers the exact Balto status line immediately and the
        # client drives POST /views/synthesize (steps 1-4 live there: saved-view coverage check,
        # member-catalog gate, build_view generation, draft id). No tool runs in this layer and
        # nothing is persisted by the turn itself.
        if detect_view_intent(message):
            return Turn(
                answer=BALTO_STATUS,
                view_intent=True,
                view_request=message,
                slots=slots,
                needs_disambiguation=ambiguous,
                session_id=self.session.id,
                tenant_id=self.tenant_id,
            )

        # EXPLICIT GATE (TODO AI/P1): the regex action-routing is the OFFLINE FACADE — FakeRuntime
        # only. On any real runtime (ManagedAgentsRuntime) the coordinator picks the tools; its
        # custom_tool_use events drive the routing below.
        if not isinstance(self.runtime, FakeRuntime):
            return self._handle_coordinator(message, slots, ambiguous, action_kwargs)

        action_name, tool_cls = self._match_action(message)
        if tool_cls is not None:
            return self._handle_action(message, action_name, tool_cls, slots, ambiguous, action_kwargs)

        return self._handle_knowledge(message, slots, ambiguous)

    def _handle_coordinator(self, message, slots, ambiguous, action_kwargs) -> Turn:
        """Real-runtime turn: tool selection comes from the COORDINATOR, never a local regex —
        and tool EXECUTION belongs to the runtime's single executor, never to this layer
        (docs/decisions/custom-tool-execution-path.md: the deployed EnvironmentWorker on
        ManagedAgentsRuntime; the runtime's own loop on the self-hosted HIPAA fallback).

        `send_message` returns the event digest: answer text, delegations
        (session.thread_created), `tool_results` (calls the executor served this turn) and
        `pending_approvals` (already-routed `tool_name` entries + any call that reached the
        digest UN-served). Handling:

        - `tool_results` are recorded to analytics — the work already happened server-side,
          NOTHING is re-run here;
        - an already-routed `tool_name` entry (a gated call's Greenlight proposal, built by
          `Tool.invoke` in the executor — draft-only) passes through untouched; its approval is
          recorded to analytics. The proposal is never enqueued twice;
        - every other pending entry (read-only/side-effecting/unknown `tool` events that nothing
          served — worker down, unknown name) is surfaced UNTOUCHED: this layer never resolves a
          name through the registry, never invokes, never default-allows.

        `action_kwargs` is facade-only (FakeRuntime regex path): with one server-side executor
        there is no in-process invocation to top up, so explicit caller args are ignored here.
        """
        del action_kwargs  # facade-only (see docstring) — nothing is invoked in this layer

        resp = self.runtime.send_message(self.session, message)

        # Executor-served calls this turn (worker / self-hosted loop) — recorded for the trace;
        # the results were fed back into the session by the executor, nothing re-runs.
        for tr in resp.get("tool_results") or []:
            self._record(EventType.TOOL_CALL, {"tool": tr.get("tool"), "status": tr.get("status")})

        pending: list[dict] = []
        for event in resp.get("pending_approvals") or []:
            pending.append(event)  # surfaced verbatim — served or not, nothing executes here
            if isinstance(event, dict) and event.get("tool_name"):
                approval = event.get("approval")
                if isinstance(approval, dict):
                    self._record(EventType.APPROVAL, {
                        "approval_id": approval.get("id"), "action": event.get("tool_name"),
                    })

        answer = resp.get("answer") or ""
        citations: list[dict] = []
        # GROUNDED CITATIONS ON THE LIVE PATH: a knowledge-shaped turn (nothing queued for
        # approval) runs the SAME citation-invariant RAG path the FakeRuntime facade uses
        # (_grounded_answer -> conv.rag.answer over the tenant-scoped PgRagClient), so the chat
        # answer carries verifiable citations. Only GROUNDED citations attach — assembly already
        # dropped any claim whose source_ref does not exist in the retrieved set, and the
        # `c.source_ref` filter additionally strips flag_uncited markers (empty refs): an
        # uncited claim is never surfaced as grounded. When the coordinator produced no prose,
        # the grounded extract stands in. Action turns skip retrieval entirely (no needless
        # vector search + synthesizer call per approval round-trip).
        if self.rag is not None and not pending:
            grounded = self._grounded_answer(message)
            citations = [c.as_dict() for c in grounded.citations if c.source_ref]
            if not answer and citations:
                answer = grounded.answer
        if not answer and pending:
            answer = "Prepared an action for your approval."
        return Turn(
            answer=answer,
            citations=citations,
            pending_approvals=pending,
            slots=slots,
            needs_disambiguation=ambiguous,
            delegations=resp.get("delegations", []),
            session_id=self.session.id,
            tenant_id=self.tenant_id,
        )

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
            ans = self._grounded_answer(message)
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
