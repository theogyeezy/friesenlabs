"""The swappable agent-runtime adapter (Build Guide Phase 4).

"Own your design, rent the runtime." Agent definitions, prompts, tool schemas, and control policies
live in this repo as code; the runtime only executes them. Everything goes through `AgentRuntime` so
Managed Agents (today) can be swapped for a Bedrock/1P fallback (HIPAA tenants) without touching
callers.

NOTHING here calls real Anthropic on import or construction. `ManagedAgentsRuntime` builds its client
lazily and every method is flagged "verify" (MA is beta). Tests use `FakeRuntime`.
"""
from __future__ import annotations

import abc
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from shared.config import MA_BETA_HEADER

# Hard multi-agent limits (Build Guide Step 24, "THE HARD MULTI-AGENT LIMITS").
DELEGATION_DEPTH = 1          # no nested sub-teams
MAX_AGENTS_PER_ROSTER = 20
MAX_CONCURRENT_THREADS = 25

# Client-side custom-tool execution bound (docs/decisions/custom-tool-execution-path.md,
# ratified #123): max execute-and-resume rounds per send_message turn. Each round may carry
# several parallel AUTO tool calls; on exhaustion the loop fails CLOSED (surfaces the calls as
# pending and returns) rather than draining a runaway coordinator forever.
DEFAULT_MAX_TOOL_ROUNDS = 8

# Reconnect-with-consolidation bound (the ratified brief's named risk: an SSE drop while a
# custom_tool_use round is in flight is a documented deadlock). On a connection-shaped stream
# failure mid-turn, `send_message` re-opens the session stream ONCE, replays the gap via
# `events.list` (deduped by server event id), and resumes the drain. A second drop fails loud.
MAX_STREAM_RECONNECTS = 1


def _is_stream_drop(exc: BaseException) -> bool:
    """True only for connection-shaped failures of an open SSE stream — the reconnectable class.
    anthropic's APIConnectionError covers SDK transport drops (APITimeoutError subclasses it);
    ConnectionError/TimeoutError cover raw socket teardown. Anything else — including this
    adapter's own RuntimeErrors (terminated, retries_exhausted, result-submission failures) —
    is NOT a drop and propagates unchanged."""
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    try:
        from anthropic import APIConnectionError  # noqa: PLC0415 — lazy on purpose
    except Exception:  # SDK absent (offline test envs) — the builtin classes above still apply
        return False
    return isinstance(exc, APIConnectionError)

# The MA built-in toolset id implied for every agent (versioned, static resource).
# VERIFY: toolset version string against the live managed-agents-2026-04-01 surface.
AGENT_TOOLSET = "agent_toolset_20260401"


@dataclass
class Session:
    id: str
    tenant_id: str
    coordinator_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentRuntime(abc.ABC):
    """Everything the rest of the system needs from the agent plane."""

    @abc.abstractmethod
    def create_environment(self, name: str) -> str: ...

    @abc.abstractmethod
    def create_agent(self, spec: "Any") -> str: ...

    @abc.abstractmethod
    def create_coordinator(self, spec: "Any", agent_ids: list[str]) -> str: ...

    @abc.abstractmethod
    def create_vault(self, display_name: str, external_user_id: str) -> str: ...

    @abc.abstractmethod
    def create_session(self, coordinator_id: str, tenant_id: str, vault_id: str | None = None,
                       environment_id: str | None = None) -> Session: ...

    @abc.abstractmethod
    def send_message(self, session: Session, message: str) -> dict[str, Any]: ...


class ManagedAgentsRuntime(AgentRuntime):
    """Real Claude Managed Agents adapter. BETA — never exercised against live Anthropic in tests
    (tests inject a mocked client); every assumed SDK shape carries a `# VERIFY:` flag. The org API
    key creates sessions/agents; it must never reach the worker (the worker holds the env key only).

    Environment binding is PER TENANT: `create_session(..., environment_id=...)` takes the
    persisted id for THAT tenant (resolved from the WorkspaceStore by the caller). The
    constructor/`create_environment` id is only a single-tenant convenience fallback — an
    instance-global must never silently serve every tenant, and `create_environment` refuses to
    overwrite an already-configured id.

    CLIENT-SIDE TOOL EXECUTION (docs/decisions/custom-tool-execution-path.md, ratified #123 —
    v1 = client-side; the orchestrator drives tool execution): when `tool_context_factory` is
    injected (the same seam `SelfHostedToolUseRuntime` carries), `send_message` executes the
    coordinator's read-only (Policy.AUTO) custom-tool calls IN-PROCESS through the trusted
    registry and feeds each result back as `user.custom_tool_result` — the documented round-trip
    for `{"type": "custom"}` tools.

    ALWAYS_ASK tools are NEVER executed here — but per the ratified brief they are also never
    left dangling: when the bound ToolContext carries a Greenlight client, a gated call is
    ROUTED to Greenlight via `Tool.invoke` (the Phase 4 base class builds the proposal and never
    performs the side effect) and the session receives an IMMEDIATE `user.custom_tool_result`
    reply — `{"status": "queued_for_approval", "approval_id", "performed": false}` — so the
    coordinator can acknowledge the queue in its answer instead of blocking forever on the tool
    call. The routed call surfaces as an already-routed pending entry (`tool_name`, NOT `tool` —
    the same contract `SelfHostedToolUseRuntime` uses, so `conv.session` never re-invokes it).
    Without a Greenlight client there is nothing to truthfully queue into, so the gated call
    keeps the pre-brief behavior: surfaced as a `tool` entry for `conv.session`'s own routing.
    Unknown tools are never default-allowed: surfaced untouched, nothing runs.

    With no factory — or a context carrying no tool clients — behavior is byte-identical to the
    pre-execution adapter: events surface, nothing runs.
    """

    def __init__(
        self,
        api_key: str | None = None,
        environment_id: str | None = None,
        *,
        tool_context_factory: Callable[["Session"], Any] | None = None,
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    ):
        if max_tool_rounds < 1:
            raise ValueError(f"max_tool_rounds must be >= 1, got {max_tool_rounds}")
        self._api_key = api_key
        self._client = None  # built lazily; import never needs the network
        self._environment_id = environment_id
        self._coordinator_ids: set[str] = set()  # coordinators created here (depth-1 guard)
        self._session_ids: set[str] = set()      # sessions opened here (thread-cap guard)
        # The client-side execution seam: session -> tenant-bound ToolContext. PUBLIC on purpose —
        # `conv.session.Conversation` binds its per-tenant context builder here when the caller
        # didn't inject one (None = no executor = today's surface-only behavior).
        self.tool_context_factory = tool_context_factory
        self.max_tool_rounds = max_tool_rounds
        # Reconnect consolidation ledger: per-session set of server event ids already processed.
        # `events.list` replays the FULL session log, so dedupe must span turns on this instance
        # (one runtime per conversation in prod — bounded by the conversation's lifetime).
        self._seen_event_ids: dict[str, set[str]] = {}

    def _c(self):
        if self._client is None:
            from anthropic import Anthropic  # noqa: PLC0415 — lazy on purpose

            # VERIFY: beta namespace + header shape against the live SDK before use.
            self._client = Anthropic(
                api_key=self._api_key,
                default_headers={"anthropic-beta": MA_BETA_HEADER},
            )
        return self._client

    @staticmethod
    def _beta_headers() -> dict[str, str]:
        # The SDK sets the MA beta header automatically on client.beta.* calls; we also pass it
        # explicitly per the repo convention ("MA beta header on every Anthropic call") so a header
        # regression in the SDK or a future namespace move cannot silently drop it.
        return {"anthropic-beta": MA_BETA_HEADER}

    @staticmethod
    def _tool_specs(spec: Any) -> list[dict]:
        """AgentSpec.tools name-strings -> MA tool definitions via the trusted registry +
        Tool.to_spec(). The built-in agent toolset is implied for every agent (roster contract)."""
        from .tools import registry  # noqa: PLC0415 — lazy: keep module import cheap

        tools: list[dict] = [{"type": AGENT_TOOLSET}]
        tools.extend(registry.resolve(name).to_spec() for name in (getattr(spec, "tools", None) or []))
        return tools

    @staticmethod
    def _stop_reason_type(event: Any) -> str | None:
        stop = getattr(event, "stop_reason", None)
        if stop is None:
            return None
        if isinstance(stop, dict):
            return stop.get("type")
        return getattr(stop, "type", None)

    # ------------------------------------------------- client-side AUTO-tool execution helpers
    @staticmethod
    def _auto_tool_class(name: str | None):
        """TRUSTED-registry lookup for client-side execution: returns the Tool class ONLY for a
        known read-only (Policy.AUTO) tool. ALWAYS_ASK and unknown names return None — they are
        never executed here (unknown is never default-allowed; gated tools keep their existing
        surface-to-Greenlight path in conv.session)."""
        if not name:
            return None
        from .tools.base import Policy  # noqa: PLC0415 — lazy: keep module import cheap
        from .tools.registry import get_tool  # noqa: PLC0415

        cls = get_tool(name)
        if cls is None or cls.policy is not Policy.AUTO:
            return None
        return cls

    @staticmethod
    def _ctx_has_clients(ctx: Any) -> bool:
        """Honest fallback gate: a ToolContext carrying NO tool clients must not 'execute' tools
        into empty results the coordinator would then present as data-grounded. No clients ->
        no execution -> events surface exactly as before."""
        return any(getattr(ctx, name, None) is not None for name in ("db", "cube", "rag", "cortex"))

    def _run_auto_tool(self, ctx: Any, entry: dict, thread_id: str | None) -> tuple[dict, str]:
        """Execute ONE coordinator-requested AUTO tool via the trusted registry; return the
        `user.custom_tool_result` event to feed back + an "ok"/"error" status for the digest.

        Fails CLOSED: any executor error (bad model input, degraded client) becomes an
        `is_error` tool result fed back to the session — the drain loop never crashes mid-turn.
        Result shape per the MA docs (events reference): {type, custom_tool_use_id,
        content:[{type:"text",text}], is_error?}; `session_thread_id` is echoed when the call
        was cross-posted from a subagent thread (multiagent contract).
        """
        name, tu_id = entry.get("tool"), entry.get("custom_tool_use_id")
        try:
            cls = self._auto_tool_class(name)
            if cls is None:  # routing guarantees AUTO-only; double-checked here (never default-allow)
                raise RuntimeError(f"{name!r} is not an executable AUTO registry tool")
            out = cls().invoke(ctx, **(entry.get("input") or {}))
            content, status = json.dumps(out.get("result"), default=str), "ok"
        except Exception as exc:  # fail closed: error result fed back, never a crash
            content, status = f"{name} failed: {exc}", "error"
        result: dict[str, Any] = {
            "type": "user.custom_tool_result",
            "custom_tool_use_id": tu_id,
            "content": [{"type": "text", "text": content}],
        }
        if status == "error":
            result["is_error"] = True
        if thread_id is not None:
            result["session_thread_id"] = thread_id
        return result, status

    @staticmethod
    def _gated_tool_class(name: str | None):
        """TRUSTED-registry lookup for runtime-side Greenlight routing: returns the Tool class
        ONLY for a known side-effecting (Policy.ALWAYS_ASK) tool. Unknown names return None —
        never default-allowed."""
        if not name:
            return None
        from .tools.base import Policy  # noqa: PLC0415 — lazy: keep module import cheap
        from .tools.registry import get_tool  # noqa: PLC0415

        cls = get_tool(name)
        if cls is None or cls.policy is not Policy.ALWAYS_ASK:
            return None
        return cls

    def _route_gated_tool(self, ctx: Any, entry: dict, thread_id: str | None) -> tuple[dict, str, dict | None]:
        """Route ONE coordinator-requested ALWAYS_ASK tool to Greenlight and build the IMMEDIATE
        `user.custom_tool_result` reply the ratified brief prescribes ("queued for human
        approval, not performed") — the coordinator session is never left dangling on a gated
        call. `Tool.invoke` (Phase 4 base class) builds the proposal and routes it to Greenlight
        WITHOUT performing the side effect; the real action only ever runs through the existing
        post-approval gate.

        Returns (result_event, digest_status, already_routed_pending_entry | None). Fails CLOSED
        like the AUTO path: a routing error becomes an `is_error` reply fed back — never a crash,
        and nothing lands in the queue.
        """
        name, tu_id = entry.get("tool"), entry.get("custom_tool_use_id")
        routed: dict | None = None
        try:
            cls = self._gated_tool_class(name)
            if cls is None:  # routing guarantees ALWAYS_ASK-only; double-checked (never default-allow)
                raise RuntimeError(f"{name!r} is not a routable ALWAYS_ASK registry tool")
            out = cls().invoke(ctx, **(entry.get("input") or {}))  # proposal only — never the side effect
            approval = out.get("approval") or {}
            # The already-routed entry (`tool_name`, NOT `tool`) — the same contract
            # SelfHostedToolUseRuntime uses, so conv.session never enqueues the proposal twice.
            routed = {
                "status": "pending_approval",
                "tool_name": name,
                "input": entry.get("input"),
                "custom_tool_use_id": tu_id,
                "proposal": out.get("proposal"),
            }
            if out.get("approval") is not None:
                routed["approval"] = out["approval"]
            content, status = json.dumps({
                "status": "queued_for_approval",
                "approval_id": approval.get("id"),
                "performed": False,
                "detail": f"{name} is side-effecting: the call was queued for human approval "
                          "(Greenlight) and was NOT performed. Report it as awaiting approval; "
                          "do not retry.",
            }), "queued_for_approval"
        except Exception as exc:  # fail closed: error result fed back, never a crash
            routed = None
            content, status = f"{name} failed: {exc}", "error"
        result: dict[str, Any] = {
            "type": "user.custom_tool_result",
            "custom_tool_use_id": tu_id,
            "content": [{"type": "text", "text": content}],
        }
        if status == "error":
            result["is_error"] = True
        if thread_id is not None:
            result["session_thread_id"] = thread_id
        return result, status, routed

    def create_environment(self, name: str) -> str:
        # Guard: never silently overwrite a configured environment id (the persisted per-tenant id
        # from the WorkspaceStore). A runtime bound to tenant X's environment must not be repointed
        # at a fresh one mid-flight — provision on a fresh runtime instead.
        if self._environment_id is not None:
            raise RuntimeError(
                f"this runtime is already bound to environment {self._environment_id!r}; "
                "refusing to create (and overwrite it with) a new environment — provision on a "
                "fresh ManagedAgentsRuntime instance"
            )
        # VERIFY: POST /v1/environments — self-hosted config is the bare {"type": "self_hosted"}
        # (no networking/packages sub-fields apply); tool execution stays in our VPC via
        # worker/worker.py polling this environment's work queue with the env key.
        env = self._c().beta.environments.create(
            name=name,
            config={"type": "self_hosted"},
            extra_headers=self._beta_headers(),
        )
        self._environment_id = env.id
        return env.id

    def create_agent(self, spec) -> str:
        # VERIFY: client.beta.agents.create(name, model, system, tools=[...]) — flat fields on the
        # agent object (never on the session); returns a versioned, persistent agent id.
        agent = self._c().beta.agents.create(
            name=spec.name,
            model=spec.model,
            system=spec.system,
            tools=self._tool_specs(spec),
            extra_headers=self._beta_headers(),
        )
        return agent.id

    def create_coordinator(self, spec, agent_ids) -> str:
        agent_ids = list(agent_ids)
        # HARD LIMIT: <= 20 agents on a roster.
        if len(agent_ids) > MAX_AGENTS_PER_ROSTER:
            raise ValueError(
                f"roster of {len(agent_ids)} agents exceeds the MA limit of {MAX_AGENTS_PER_ROSTER}"
            )
        # HARD LIMIT: delegation depth 1 / flat topology — a coordinator's roster may not contain
        # another coordinator (MA silently ignores depth > 1; we fail loudly instead).
        nested = sorted(a for a in agent_ids if a in self._coordinator_ids)
        if nested:
            raise ValueError(
                f"delegation depth is {DELEGATION_DEPTH} (flat topology): coordinators cannot "
                f"delegate to other coordinators: {nested}"
            )
        # VERIFY: multiagent is a TOP-LEVEL agents.create field (not a tools[] entry):
        # {"type": "coordinator", "agents": [<id strings>]} — string entries pin the latest version.
        coordinator = self._c().beta.agents.create(
            name=spec.name,
            model=spec.model,
            system=spec.system,
            tools=self._tool_specs(spec),
            multiagent={"type": "coordinator", "agents": agent_ids},
            extra_headers=self._beta_headers(),
        )
        self._coordinator_ids.add(coordinator.id)
        return coordinator.id

    def create_vault(self, display_name, external_user_id) -> str:
        # VERIFY: vault create field naming — sibling MA resources (environments/agents/memory
        # stores) take `name`, while vault *credentials* take `display_name`; confirm the live
        # client.beta.vaults.create signature before use. Vaults are workspace-scoped (the
        # per-tenant isolation boundary); metadata carries the external user mapping.
        vault = self._c().beta.vaults.create(
            name=display_name,
            metadata={"external_user_id": external_user_id},
            extra_headers=self._beta_headers(),
        )
        return vault.id

    def create_session(self, coordinator_id, tenant_id, vault_id=None, environment_id=None) -> Session:
        # PER-TENANT environment binding: the caller resolves THIS tenant's persisted environment
        # id (WorkspaceStore row) and passes it here. The instance-level id is only a fallback for
        # single-tenant/dev runtimes — it must never silently serve every tenant in a pooled API.
        env_id = environment_id or self._environment_id
        if env_id is None:
            raise RuntimeError(
                "create_session needs an environment_id — pass the tenant's persisted id "
                "(WorkspaceStore row), call create_environment() first, or construct "
                "ManagedAgentsRuntime(environment_id=...) for single-tenant use"
            )
        # HARD LIMIT: <= 25 concurrent threads. Client-side guard: each session holds at least one
        # live thread, so this adapter refuses to hold more than 25 open sessions at once.
        # VERIFY: the server-side cap is per-session subagent threads; revisit this guard once the
        # live thread-accounting semantics are confirmed.
        if len(self._session_ids) >= MAX_CONCURRENT_THREADS:
            raise RuntimeError(
                f"refusing to open another session: {len(self._session_ids)} already open on this "
                f"runtime (MA concurrent-thread limit is {MAX_CONCURRENT_THREADS})"
            )
        # THE TRUST RULE: tenant_id arrives from the caller (verified Cognito JWT claim upstream) —
        # never read from env/header/payload here. It rides in session metadata so the worker can
        # bind app.current_tenant (RLS) during tool execution.
        metadata: dict[str, str] = {"tenant_id": tenant_id}
        kwargs: dict[str, Any] = dict(
            agent=coordinator_id,  # VERIFY: string shorthand pins the agent's latest version
            environment_id=env_id,
            metadata=metadata,
            extra_headers=self._beta_headers(),
        )
        if vault_id is not None:
            metadata["vault_id"] = vault_id
            kwargs["vault_ids"] = [vault_id]  # VERIFY: vault attach is session-create-only
        s = self._c().beta.sessions.create(**kwargs)
        self._session_ids.add(s.id)
        return Session(
            id=s.id,
            tenant_id=tenant_id,
            coordinator_id=coordinator_id,
            metadata={"tenant_id": tenant_id, "vault_id": vault_id},
        )

    def send_message(self, session, message) -> dict:
        """One turn against the live session as the real event-stream flow:
        stream-first -> send -> drain-to-idle, WITH client-side resolution of the coordinator's
        custom-tool calls (custom-tool-execution-path decision, ratified #123).

        The documented custom-tool round-trip: `agent.custom_tool_use` -> session idles with
        stop_reason `requires_action` -> this orchestrator resolves the round through the
        trusted registry with the session's tenant-bound ToolContext -> `user.custom_tool_result`
        goes back via `events.send` on the SAME open stream (client-patterns Pattern 9 submits
        results while the stream stays open) -> the drain continues until the coordinator
        settles. Per-call resolution rules:

        - registry Policy.AUTO tools EXECUTE, but only when `tool_context_factory` is bound AND
          the context carries at least one tool client (honest fallback: no clients -> no
          execution -> byte-identical surface-only behavior);
        - registry ALWAYS_ASK tools are NEVER executed; when the context carries a Greenlight
          client they are ROUTED — `Tool.invoke` lands the proposal in Greenlight (draft-only,
          base-class guarantee) and the session gets an IMMEDIATE reply
          `{"status": "queued_for_approval", "approval_id", "performed": false}` (ratified
          brief: the coordinator acknowledges the queue instead of dangling on the call). The
          routed call surfaces as an already-routed `tool_name` entry. With NO Greenlight client
          there is nothing to truthfully queue into — the call surfaces as a `tool` entry for
          conv.session's routing, exactly the pre-brief behavior;
        - unknown tools are never default-allowed: surfaced untouched, nothing resolves;
        - a round resolves ONLY when EVERY call in it is resolvable — partial result submission
          to a session still blocked on an unresolvable call is deliberately avoided (VERIFY:
          live partial-fulfilment resume semantics before ever relying on them);
        - the loop is BOUNDED at `max_tool_rounds` resolve-and-resume rounds; on exhaustion the
          remaining calls surface as pending (reason: max_tool_rounds_exhausted) and the turn
          returns — fail closed, never an unbounded drain;
        - THE TRUST RULE: the execution tenant comes from the factory's ToolContext, which is
          built from SESSION metadata only (set from the verified claim at create_session).

        RECONNECT-WITH-CONSOLIDATION (the ratified brief's named deadlock): if the SSE stream
        drops mid-turn (connection-shaped failure only — `_is_stream_drop`), the turn re-opens
        the session stream (bounded: MAX_STREAM_RECONNECTS), THEN replays the gap via
        `events.list`, deduping by server event id so nothing is double-counted and an
        already-answered tool call is never collected or answered twice (for
        `agent.custom_tool_use` the event id IS the custom_tool_use id), and resumes the drain
        on the fresh stream. A result-submission failure is NOT retried — a blind resubmission
        could double-deliver results — and a second drop fails loud. VERIFY: events.list
        pagination/order + server event-id presence on every event against the live SDK
        (consolidation relies on those ids).

        Returns the FakeRuntime-compatible shape {session_id, tenant_id, delegations, answer}
        plus `pending_approvals` (surfaced events + already-routed `tool_name` entries) and
        `tool_results` (resolved calls: {tool, custom_tool_use_id, status} with status
        ok | error | queued_for_approval).
        """
        client = self._c()
        answer_parts: list[str] = []
        delegations: list[str] = []
        pending: list[dict] = []
        tool_results: list[dict] = []
        errors: list[str] = []
        # The current round's surfaced-but-unresolved tool calls, in ARRIVAL ORDER:
        # (pending-entry, session_thread_id, AUTO class | None, ALWAYS_ASK class | None).
        # Resolution happens at the requires_action idle gate — parallel calls batch into one
        # events.send.
        calls: list[tuple[dict, str | None, Any, Any]] = []
        rounds = 0
        reconnects = 0
        seen = self._seen_event_ids.setdefault(session.id, set())

        def handle(event) -> bool:
            """Process ONE session event (live stream or replay); True = the turn is over.
            Events carrying a server id are deduped against the per-session ledger, so a
            replayed/overlapping event is processed exactly once."""
            nonlocal rounds
            eid = getattr(event, "id", None)
            if eid is not None:
                if eid in seen:
                    return False  # already processed (reconnect replay / stream-list overlap)
                seen.add(eid)
            etype = getattr(event, "type", None)
            if etype == "agent.message":
                for block in getattr(event, "content", None) or []:
                    if getattr(block, "type", None) == "text":
                        answer_parts.append(getattr(block, "text", "") or "")
            elif etype == "session.thread_created":
                # A coordinator delegation spawned a specialist thread.
                # VERIFY: event carries `agent_name` per the multiagent event payloads.
                name = getattr(event, "agent_name", None)
                if name:
                    delegations.append(name)
            elif etype == "agent.custom_tool_use":
                # A client-side tool call. Collected (never resolved inline) — the
                # requires_action idle gate decides: resolve-and-resume for fully-resolvable
                # rounds, surface-as-pending otherwise. Entry shape is unchanged from the
                # pre-execution adapter so surfaced events stay byte-identical.
                calls.append((
                    {
                        "status": "pending",
                        "tool": getattr(event, "name", None),
                        "input": getattr(event, "input", None),
                        "custom_tool_use_id": getattr(event, "id", None),
                    },
                    getattr(event, "session_thread_id", None),
                    self._auto_tool_class(getattr(event, "name", None)),
                    self._gated_tool_class(getattr(event, "name", None)),
                ))
            elif etype == "session.error":
                errors.append(str(getattr(event, "error", None) or getattr(event, "message", "")))
            elif etype == "session.status_terminated":
                raise RuntimeError(
                    f"MA session {session.id} terminated (irreversible)"
                    + (f" — errors: {errors}" if errors else "")
                )
            elif etype == "session.status_idle":
                # Drain-to-idle gate. "requires_action" = blocked on a client-side event: a
                # fully-resolvable round (AUTO executes / ALWAYS_ASK routes to Greenlight with
                # an immediate queued_for_approval reply) resolves here and the drain CONTINUES;
                # anything unknown/unresolvable surfaces as pending and the turn returns (the
                # session stays blocked exactly as before — approval flows resolve it
                # out-of-band). Every other idle ends the turn.
                stop = self._stop_reason_type(event)
                if stop == "retries_exhausted":
                    raise RuntimeError(
                        f"MA session {session.id} idle after retries_exhausted"
                        + (f" — errors: {errors}" if errors else "")
                    )
                if stop == "requires_action" and calls:
                    ctx = (
                        self.tool_context_factory(session)
                        if self.tool_context_factory is not None else None
                    )
                    resolvable = ctx is not None and all(
                        (auto_cls is not None and self._ctx_has_clients(ctx))
                        or (gated_cls is not None
                            and getattr(ctx, "greenlight", None) is not None)
                        for _entry, _tid, auto_cls, gated_cls in calls
                    )
                    if resolvable and rounds < self.max_tool_rounds:
                        rounds += 1
                        results = []
                        for entry, thread_id, auto_cls, _gated_cls in calls:
                            if auto_cls is not None:
                                result, status = self._run_auto_tool(ctx, entry, thread_id)
                            else:
                                result, status, routed = self._route_gated_tool(
                                    ctx, entry, thread_id
                                )
                                if routed is not None:
                                    pending.append(routed)
                            results.append(result)
                            tool_results.append({
                                "tool": entry.get("tool"),
                                "custom_tool_use_id": entry.get("custom_tool_use_id"),
                                "status": status,
                            })
                        calls.clear()
                        try:
                            client.beta.sessions.events.send(
                                session_id=session.id,
                                events=results,
                                extra_headers=self._beta_headers(),
                            )
                        except Exception as exc:
                            # NOT reconnectable: whether the batch reached the server is
                            # unknowable here, and a blind resubmission could double-deliver.
                            raise RuntimeError(
                                f"tool-result submission failed for MA session {session.id} — "
                                "failing loud (results are never blindly resubmitted)"
                            ) from exc
                        return False  # keep draining the SAME stream to the next idle
                    if resolvable:  # bound hit — fail closed: surface, never drain forever
                        for entry, _tid, _a, _g in calls:
                            entry["reason"] = "max_tool_rounds_exhausted"
                    pending.extend(entry for entry, _tid, _a, _g in calls)
                    calls.clear()
                    return True
                if stop == "requires_action" and not pending:
                    pending.append({"status": "pending", "reason": "requires_action"})
                return True
            return False

        done = False
        while True:
            # Stream-FIRST, then send: the SSE stream only delivers events emitted after it
            # opens — send-then-stream loses the early events. The same order holds on
            # reconnect: open the fresh stream first, then replay the gap (no second gap).
            with client.beta.sessions.events.stream(
                session_id=session.id, extra_headers=self._beta_headers()
            ) as stream:
                if reconnects == 0:
                    client.beta.sessions.events.send(
                        session_id=session.id,
                        events=[{
                            "type": "user.message",
                            "content": [{"type": "text", "text": message}],
                        }],
                        extra_headers=self._beta_headers(),
                    )
                else:
                    # CONSOLIDATION: the previous stream dropped mid-turn. Replay everything
                    # the session emitted while we were dark; `handle` dedupes by event id, so
                    # already-processed events — including already-answered tool calls — are
                    # skipped. VERIFY: events.list shape/order against the live SDK.
                    for event in client.beta.sessions.events.list(
                        session_id=session.id, extra_headers=self._beta_headers()
                    ):
                        if handle(event):
                            done = True
                            break
                if not done:
                    try:
                        for event in stream:
                            if handle(event):
                                done = True
                                break
                    except Exception as exc:
                        if not _is_stream_drop(exc):
                            raise
                        if reconnects >= MAX_STREAM_RECONNECTS:
                            raise RuntimeError(
                                f"MA session {session.id} stream dropped again after "
                                f"{reconnects} reconnect(s) — giving up (bounded retry)"
                            ) from exc
                        reconnects += 1
                        continue  # re-open the stream, replay the gap, resume the drain
            break
        # Defensive: anything still unresolved at stream end surfaces (never silently dropped).
        pending.extend(entry for entry, _tid, _a, _g in calls)
        return {
            "session_id": session.id,
            "tenant_id": session.tenant_id,
            "delegations": delegations,
            "answer": "".join(answer_parts),
            "pending_approvals": pending,
            "tool_results": tool_results,
        }


class FakeRuntime(AgentRuntime):
    """In-memory runtime for tests/dev. Records what was created and simulates a coordinator that
    delegates to specialists and surfaces tool calls — no network, no Anthropic.
    """

    def __init__(self):
        self.environments: list[str] = []
        self.agents: dict[str, Any] = {}
        self.coordinators: dict[str, list[str]] = {}
        self.vaults: list[str] = []
        self.sessions: dict[str, Session] = {}
        self.sent: list[tuple[str, str]] = []
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}_{self._n}"

    def create_environment(self, name: str) -> str:
        eid = self._id("env")
        self.environments.append(eid)
        return eid

    def create_agent(self, spec) -> str:
        aid = self._id("agent")
        self.agents[aid] = spec
        return aid

    def create_coordinator(self, spec, agent_ids) -> str:
        assert len(agent_ids) <= MAX_AGENTS_PER_ROSTER, "roster exceeds 20"
        cid = self._id("coord")
        self.agents[cid] = spec
        self.coordinators[cid] = list(agent_ids)
        return cid

    def create_vault(self, display_name, external_user_id) -> str:
        vid = self._id("vault")
        self.vaults.append(vid)
        return vid

    def create_session(self, coordinator_id, tenant_id, vault_id=None, environment_id=None) -> Session:
        s = Session(
            id=self._id("sess"),
            tenant_id=tenant_id,
            coordinator_id=coordinator_id,
            metadata={"tenant_id": tenant_id, "vault_id": vault_id,
                      "environment_id": environment_id},
        )
        self.sessions[s.id] = s
        return s

    def send_message(self, session, message) -> dict:
        self.sent.append((session.id, message))
        # Simulate the coordinator delegating to every specialist on its roster.
        roster = self.coordinators.get(session.coordinator_id, [])
        return {
            "session_id": session.id,
            "tenant_id": session.tenant_id,
            "delegations": [self.agents[a].name for a in roster if a in self.agents],
            "answer": f"[fake] handled: {message}",
        }


def get_runtime(config: dict[str, Any] | None = None) -> AgentRuntime:
    """Factory: pick the runtime impl. Defaults to fake unless explicitly configured:

    - 'managed': Claude Managed Agents (the standard tenancy path);
    - 'self_hosted': the HIPAA fallback — a direct Anthropic Messages tool-use loop over the
      SAME registry tools with the SAME Greenlight ALWAYS_ASK routing, no Managed Agents
      (MA-on-Bedrock does not exist; see agents/runtime_selfhosted.py for the decision record).
    """
    config = config or {}
    kind = config.get("runtime", "fake")
    if kind == "managed":
        return ManagedAgentsRuntime(
            api_key=config.get("api_key"),
            environment_id=config.get("environment_id"),  # the persisted per-tenant env id
            # Client-side AUTO-tool execution seam (ratified #123). Optional: when absent,
            # conv.session.Conversation binds its tenant-scoped context builder post-construction.
            tool_context_factory=config.get("tool_context_factory"),
            max_tool_rounds=config.get("max_tool_rounds") or DEFAULT_MAX_TOOL_ROUNDS,
        )
    if kind == "self_hosted":
        # Lazy import keeps this module's import cost unchanged for the fake/managed paths.
        from .runtime_selfhosted import DEFAULT_MAX_TURNS, SelfHostedToolUseRuntime  # noqa: PLC0415

        return SelfHostedToolUseRuntime(
            api_key=config.get("api_key"),
            workspace_store=config.get("workspace_store"),
            tenant_id=config.get("tenant_id"),
            greenlight=config.get("greenlight"),
            tool_context_factory=config.get("tool_context_factory"),
            model=config.get("model"),
            max_turns=config.get("max_turns") or DEFAULT_MAX_TURNS,
        )
    if kind == "fake":
        return FakeRuntime()
    raise ValueError(f"unknown runtime: {kind!r}")
