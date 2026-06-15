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
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from shared.config import MA_BETA_HEADER

# Hard multi-agent limits (Build Guide Step 24, "THE HARD MULTI-AGENT LIMITS").
DELEGATION_DEPTH = 1          # no nested sub-teams
MAX_AGENTS_PER_ROSTER = 20
MAX_CONCURRENT_THREADS = 25

# SETTLE budget (the agentic-turn fix, 2026-06-12): one turn may keep draining past a
# `requires_action` idle for this many wall-clock seconds (from the start of the turn) while
# the EnvironmentWorker serves the open read-only calls. Without it the turn returned at the
# FIRST requires_action — seconds before the worker answered — so the customer got "I've asked
# Scout, I'll report back" as the final answer and had to nudge the chat to harvest the result
# (live demo-tenant finding). On exhaustion the turn fails closed exactly as before. The
# default must clear the edge's 60s CloudFront-origin-read/ALB-idle ceilings with headroom.
ENV_TURN_SETTLE_SECONDS = "UPLIFT_TURN_SETTLE_SECONDS"
# Per-REQUEST: a turn that needs longer settles across /chat/continue requests (the async turn
# contract) — each request must clear the 60s edge ceilings with inference headroom.
DEFAULT_TURN_SETTLE_SECONDS = 25.0

# Bounded SSE read wait (round 4): the settle budget is only checkable when EVENTS arrive — a
# long inference round emits nothing for 40+s and a silently-blocked stream wait sails the
# request past the 60s edge ceiling into a 504. A bounded read timeout wakes the drain up
# (classified as a stream drop -> reconnect/replay, or surface unsettled once the budget is
# spent) so every request answers in time and the continue leg picks the turn back up.
ENV_STREAM_READ_SECONDS = "UPLIFT_STREAM_READ_SECONDS"
DEFAULT_STREAM_READ_SECONDS = 20.0

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
        import httpx  # noqa: PLC0415 — lazy on purpose

        # The bounded stream read timeout (round 4) surfaces as httpx.ReadTimeout when the SDK
        # hands the transport error through un-wrapped mid-iteration.
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return True
    except Exception:  # noqa: BLE001 — httpx absent: the builtin classes above still apply
        pass
    try:
        from anthropic import APIConnectionError  # noqa: PLC0415 — lazy on purpose
    except Exception:  # SDK absent (offline test envs) — the builtin classes above still apply
        return False
    return isinstance(exc, APIConnectionError)


def _is_not_found(exc: BaseException) -> bool:
    """True for a 404 / not-found from the Anthropic SDK — used by the reaper's archive so an
    already-gone agent reads as success (idempotent re-run), not a retryable failure."""
    try:
        from anthropic import NotFoundError  # noqa: PLC0415 — lazy (SDK may be absent in tests)
        if isinstance(exc, NotFoundError):
            return True
    except Exception:  # noqa: BLE001 — SDK absent: fall back to the status code
        pass
    return getattr(exc, "status_code", None) == 404


# The MA built-in toolset id (versioned, static resource). DELIBERATELY NOT GRANTED to any
# agent (live finding 2026-06-10, #147): nothing in this deployment serves native toolset calls
# — the self-hosted worker serves ONLY registry custom tools — so a granted toolset lets the
# model emit e.g. `bash` and wedge the session at requires_action forever. It would also run
# model-driven bash inside the creds-laden worker container (DB creds + env key in env). Grant
# it again only when a dedicated sandbox serves it.
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

    # Agent lifecycle management (the orphan-roster GC seam). NOT abstract: only the
    # Managed-Agents impl owns a fleet of agents to enumerate/reap — runtimes that don't
    # (self-hosted, fakes that don't need it) inherit a loud default rather than a silent no-op
    # (which would let the reaper believe it deleted something it didn't).
    def list_agents(self) -> list[dict[str, Any]]:
        raise NotImplementedError("this runtime does not manage Managed-Agents agents")

    def delete_agent(self, agent_id: str) -> None:
        raise NotImplementedError("this runtime does not manage Managed-Agents agents")


class ManagedAgentsRuntime(AgentRuntime):
    """Real Claude Managed Agents adapter. BETA — never exercised against live Anthropic in tests
    (tests inject a mocked client); every assumed SDK shape carries a `# VERIFY:` flag. The org API
    key creates sessions/agents; it must never reach the worker (the worker holds the env key only).

    Environment binding is PER TENANT: `create_session(..., environment_id=...)` takes the
    persisted id for THAT tenant (resolved from the WorkspaceStore by the caller). The
    constructor/`create_environment` id is only a single-tenant convenience fallback — an
    instance-global must never silently serve every tenant, and `create_environment` refuses to
    overwrite an already-configured id.

    SINGLE TOOL EXECUTOR = THE ENVIRONMENT WORKER (docs/decisions/custom-tool-execution-path.md;
    the deployed `worker/worker.py` EnvironmentWorker is live serving the registry tools off the
    environment work queue — the SDK's SessionToolRunner dispatches `agent.custom_tool_use` and
    posts `user.custom_tool_result` back, per the brief's critic-verified finding). This adapter
    therefore EXECUTES NOTHING: there is exactly ONE owner of tool execution, so a call can never
    be answered twice (the dual-executor double-delivery bug this removal fixes).

    What `send_message` does instead:
    - read-only (Policy.AUTO) registry tools AUTO-RUN SERVER-SIDE in the worker (in the VPC,
      tenant-bound via session metadata -> SET LOCAL); their `user.custom_tool_result` events
      are observed on the stream and recorded into the digest's `tool_results`;
    - side-effecting (ALWAYS_ASK) registry tools are routed to Greenlight BY THE WORKER —
      `Tool.invoke` (the Phase 4 base class) builds the proposal and never performs the side
      effect; the worker's result payload (`status: pending_approval`) is surfaced here as an
      already-routed entry (`tool_name`, NOT `tool` — the same contract
      `SelfHostedToolUseRuntime` uses, so `conv.session` never re-invokes it);
    - a call that reaches a `requires_action` idle UNANSWERED (worker down, unknown tool) is
      surfaced as a pending `tool` entry and the turn returns — FAIL CLOSED, nothing ever
      executes in this process, and unknown tools are never default-allowed.
    """

    def __init__(
        self,
        api_key: str | None = None,
        environment_id: str | None = None,
        clock: Callable[[], float] | None = None,
        settle_budget_s: float | None = None,
    ):
        self._api_key = api_key
        self._client = None  # built lazily; import never needs the network
        self._environment_id = environment_id
        self._coordinator_ids: set[str] = set()  # coordinators created here (depth-1 guard)
        self._session_ids: set[str] = set()      # sessions opened here (thread-cap guard)
        # Reconnect consolidation ledger: per-session set of server event ids already processed.
        # `events.list` replays the FULL session log, so dedupe must span turns on this instance
        # (one runtime per conversation in prod — bounded by the conversation's lifetime).
        self._seen_event_ids: dict[str, set[str]] = {}
        # Sessions RESUMED from a persisted id whose dedupe ledger hasn't been primed yet —
        # a fresh process knows nothing of what the dead one already delivered (see _drain).
        self._resumed_unprimed: set[str] = set()
        # SETTLE (the agentic-turn fix, 2026-06-12): how long one turn may keep draining past a
        # `requires_action` idle while the worker serves the open calls. Wall-clock from the
        # start of send_message; on exhaustion the turn fails closed exactly as before. The
        # default must clear the edge's 60s CloudFront-origin/ALB-idle ceilings with headroom.
        self._clock = clock or time.monotonic
        if settle_budget_s is None:
            settle_budget_s = float(os.environ.get(ENV_TURN_SETTLE_SECONDS, "") or
                                    DEFAULT_TURN_SETTLE_SECONDS)
        self._settle_budget_s = settle_budget_s

    def _c(self):
        if self._client is None:
            import httpx  # noqa: PLC0415 — lazy on purpose (anthropic's own transport dep)
            from anthropic import Anthropic  # noqa: PLC0415 — lazy on purpose

            read_s = float(os.environ.get(ENV_STREAM_READ_SECONDS, "") or
                           DEFAULT_STREAM_READ_SECONDS)
            # VERIFY: beta namespace + header shape against the live SDK before use.
            self._client = Anthropic(
                api_key=self._api_key,
                default_headers={"anthropic-beta": MA_BETA_HEADER},
                # Bounded read so a silent stream wait can never out-sit the edge ceiling
                # (see ENV_STREAM_READ_SECONDS above); a fired timeout is a stream drop.
                timeout=httpx.Timeout(connect=10.0, read=read_s, write=30.0, pool=10.0),
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
        Tool.to_spec(). CUSTOM TOOLS ONLY — the built-in agent toolset is deliberately NOT
        granted (see the AGENT_TOOLSET note above: nothing serves native calls, and a granted
        toolset wedges sessions at requires_action the first time the model reaches for bash)."""
        from .tools import registry  # noqa: PLC0415 — lazy: keep module import cheap

        tools: list[dict] = []
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

    # ------------------------------------------------- worker-result digest helpers
    @staticmethod
    def _result_text(event: Any) -> str:
        """Concatenate the text blocks of a `user.custom_tool_result` event (object or dict
        shaped — events.list replay may differ from the live stream's typed objects)."""
        parts: list[str] = []
        content = (event.get("content") if isinstance(event, dict)
                   else getattr(event, "content", None)) or []
        for block in content:
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if btype == "text":
                text = block.get("text") if isinstance(block, dict) else getattr(block, "text", "")
                parts.append(text or "")
        return "".join(parts)

    def _digest_tool_result(self, entry: dict, event: Any) -> tuple[str, dict | None]:
        """Map ONE worker-submitted `user.custom_tool_result` onto the digest:
        (status, already_routed_pending_entry | None).

        The worker's `SessionBoundTool.call` posts `json.dumps(Tool.invoke(...))`, so a gated
        (ALWAYS_ASK) tool's reply carries `{"status": "pending_approval", proposal, approval}`
        — the base-class draft-only guarantee ran IN THE WORKER. Such a call surfaces as the
        already-routed entry (`tool_name`, NOT `tool`) so `conv.session` passes it through
        untouched and the proposal is never enqueued twice. Anything else is a completed
        read-only run ("ok") or a worker-flagged failure ("error"). Unparseable content is
        still a served call — "ok", never a crash mid-drain."""
        is_error = (event.get("is_error") if isinstance(event, dict)
                    else getattr(event, "is_error", False))
        if is_error:
            return "error", None
        try:
            payload = json.loads(self._result_text(event))
        except (TypeError, ValueError):
            return "ok", None
        status = payload.get("status") if isinstance(payload, dict) else None
        if status in ("pending_approval", "queued_for_approval"):
            routed: dict[str, Any] = {
                "status": "pending_approval",
                "tool_name": entry.get("tool"),
                "input": entry.get("input"),
                "custom_tool_use_id": entry.get("custom_tool_use_id"),
                "proposal": payload.get("proposal"),
            }
            if payload.get("approval") is not None:
                routed["approval"] = payload["approval"]
            return "queued_for_approval", routed
        return "ok", None

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

    def list_agents(self) -> list[dict[str, Any]]:
        # VERIFY: client.beta.agents.list() returns a (paginable) iterable of agent objects with
        # flat id/name/created_at and, for coordinators, a top-level `multiagent` block
        # ({type, agents:[ids]}) — the same shape create returns. Normalized to a plain dict so the
        # reaper never depends on the beta object surface. Coordinators are flagged so a reaper can
        # tell a roster's coordinator from its specialists; `agents` lists the pinned specialist ids.
        def _ref_id(ref: Any) -> str | None:
            # multiagent.agents entries are reference OBJECTS (BetaManagedAgentsAgentReference with an
            # `.id`) live — NOT bare strings (the test mocks once used strings; live shape confirmed
            # 2026-06-15). Accept str / {"id":...} / obj.id so the reaper always gets an id string.
            if ref is None or isinstance(ref, str):
                return ref
            if isinstance(ref, dict):
                return ref.get("id")
            return getattr(ref, "id", None)

        out: list[dict[str, Any]] = []
        for a in self._c().beta.agents.list(extra_headers=self._beta_headers()):
            multi = getattr(a, "multiagent", None)
            if multi is None and isinstance(a, dict):
                multi = a.get("multiagent")
            pinned: list[str] = []
            if multi:
                raw = (multi.get("agents") if isinstance(multi, dict)
                       else getattr(multi, "agents", None)) or []
                pinned = [rid for rid in (_ref_id(x) for x in raw) if rid]
            getf = (lambda k: a.get(k)) if isinstance(a, dict) else (lambda k: getattr(a, k, None))
            out.append({
                "id": getf("id"),
                "name": getf("name"),
                "created_at": getf("created_at"),
                "is_coordinator": bool(multi),
                "agents": pinned,
            })
        return out

    def delete_agent(self, agent_id: str) -> None:
        # MA has NO hard delete — an agent is ARCHIVED (client.beta.agents.archive; confirmed live
        # 2026-06-15: client.beta.agents exposes archive/create/list/retrieve/update/versions, no
        # delete). Archived agents drop out of the default agents.list() (include_archived defaults
        # off) and free the active roster slot — exactly what the orphan reaper needs. A 404 (already
        # gone) is success so a re-run after a partial failure is idempotent. The reaper only ever
        # calls this for an agent it recorded as a SUPERSEDED roster member.
        try:
            self._c().beta.agents.archive(agent_id, extra_headers=self._beta_headers())
        except Exception as exc:  # noqa: BLE001 — 404 = already reaped (idempotent); re-raise else
            if _is_not_found(exc):
                return
            raise

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

    def resume_session(self, session_id, coordinator_id, tenant_id,
                       vault_id=None, environment_id=None) -> Session:
        """Re-attach to a PERSISTED MA session (deploy-roll survival, 2026-06-12) — constructs
        the local handle only, no network. The session may be dead server-side: the drain's
        terminated handling + the cache's rebuild path cover that (and clear the stale id).
        The dedupe ledger is primed lazily on first use so reconnect-replays never fold prior
        turns into a new digest while /chat/continue still recovers the in-flight tail."""
        self._session_ids.add(session_id)
        self._resumed_unprimed.add(session_id)
        return Session(
            id=session_id, tenant_id=tenant_id, coordinator_id=coordinator_id,
            metadata={"tenant_id": tenant_id, "environment_id": environment_id,
                      "vault_id": vault_id},
        )

    def send_message(self, session, message) -> dict:
        return self._drain(session, message)

    def continue_drain(self, session) -> dict:
        """Re-attach to an in-flight turn WITHOUT sending a user message (the async turn
        contract, 2026-06-12): a delegation-heavy turn can't settle inside one HTTP request
        under the edge's 60s ceiling, so the client continues it across short requests. The
        continue leg replays everything the session emitted while no request was attached
        (`events.list`, deduped by the per-session ledger — the same consolidation machinery
        the reconnect path uses) and then streams on until settle/budget. Observe-only:
        nothing is sent, nothing executes here."""
        return self._drain(session, None)

    def _drain(self, session, message) -> dict:
        """One request's drain against the live session as the real event-stream flow:
        stream-first -> send (initial leg only) -> drain-to-idle. THIS ADAPTER EXECUTES NO
        TOOLS — the deployed
        EnvironmentWorker is the single executor (docs/decisions/custom-tool-execution-path.md;
        see the class docstring). The drain OBSERVES the worker's round-trip on the session
        event stream:

        - `agent.custom_tool_use` is collected as an OPEN call (never resolved in-process);
        - the worker claims the call off the environment work queue, executes it in the VPC
          (read-only tools auto-run; ALWAYS_ASK tools land a Greenlight proposal via the
          Phase 4 base class — draft-only, the side effect never runs), and posts
          `user.custom_tool_result`; observing that event closes the open call into the
          digest's `tool_results` (status ok | error | queued_for_approval), and a gated
          call's reply surfaces as the already-routed `tool_name` entry
          (VERIFY: the live stream delivers user.* events to this observer — the
          consolidation replay via `events.list` does either way);
        - a `requires_action` idle with calls still OPEN means NOTHING is serving them
          (worker down, unknown tool name, a stray built-in toolset call): the open calls
          surface as pending `tool` entries and the turn returns — FAIL CLOSED. The session
          stays blocked; a recovered worker resolves it out-of-band and the per-tenant cached
          conversation surfaces the completed work on the NEXT turn (#147 continuity);
        - every other idle ends the turn; unknown tools are never default-allowed (they are
          exactly the calls that stay OPEN and surface).

        RECONNECT-WITH-CONSOLIDATION (the ratified brief's named deadlock): if the SSE stream
        drops mid-turn (connection-shaped failure only — `_is_stream_drop`), the turn re-opens
        the session stream (bounded: MAX_STREAM_RECONNECTS), THEN replays the gap via
        `events.list`, deduping by server event id so nothing is double-counted and a
        worker-answered call is never double-closed (for `agent.custom_tool_use` the event id
        IS the custom_tool_use id). A second drop fails loud. VERIFY: events.list
        pagination/order + server event-id presence on every event against the live SDK
        (consolidation relies on those ids).

        Returns the FakeRuntime-compatible shape {session_id, tenant_id, delegations, answer}
        plus `pending_approvals` (surfaced open calls + already-routed `tool_name` entries) and
        `tool_results` (worker-served calls: {tool, custom_tool_use_id, status} with status
        ok | error | queued_for_approval).
        """
        client = self._c()
        answer_parts: list[str] = []
        delegations: list[str] = []
        pending: list[dict] = []
        tool_results: list[dict] = []
        errors: list[str] = []
        # OPEN tool calls (surfaced by the coordinator, not yet answered by the worker), in
        # ARRIVAL ORDER. Closed by an observed user.custom_tool_result; anything still open at
        # a requires_action idle (or stream end) surfaces as pending — never executed here.
        calls: list[dict] = []
        # ADDITIVE token-usage observation (cost attribution): MA events MAY carry a `usage`
        # block (input/output tokens). We accumulate it defensively — any event exposing a
        # usage object contributes — and surface the running totals in the digest. Purely
        # observational: a beta stream that never emits usage simply yields {0,0} and nothing
        # downstream changes (the recorder skips zero-token turns).
        usage_in = 0
        usage_out = 0
        usage_model: str | None = None
        reconnects = 0
        turn_start = self._clock()  # settle budget anchor (see the requires_action branch)
        last_stop: str | None = None  # the most recent idle's stop reason (settle observability)
        seen = self._seen_event_ids.setdefault(session.id, set())
        if session.id in self._resumed_unprimed:
            # PRIME the resumed ledger (one events.list): a fresh process must not re-deliver
            # history. A new SEND marks EVERYTHING so far as seen (it is prior-turn material);
            # a CONTINUE marks everything through the LAST user.message — the tail after it is
            # exactly the in-flight turn the dead process never surfaced.
            self._resumed_unprimed.discard(session.id)
            history = list(client.beta.sessions.events.list(
                session_id=session.id, extra_headers=self._beta_headers()))
            if message is not None:
                cutoff = len(history)
            else:
                marks = [i for i, e in enumerate(history)
                         if getattr(e, "type", None) == "user.message"]
                cutoff = (marks[-1] + 1) if marks else 0
            for e in history[:cutoff]:
                eid = getattr(e, "id", None)
                if eid is not None:
                    seen.add(eid)

        def _accumulate_usage(event) -> None:
            nonlocal usage_in, usage_out, usage_model
            u = getattr(event, "usage", None)
            if u is None and isinstance(event, dict):
                u = event.get("usage")
            if u is None:
                return
            getu = (lambda k: u.get(k)) if isinstance(u, dict) else (lambda k: getattr(u, k, None))
            for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                v = getu(k)
                if isinstance(v, (int, float)):
                    usage_in += int(v)
            v = getu("output_tokens")
            if isinstance(v, (int, float)):
                usage_out += int(v)
            m = getattr(event, "model", None) or (event.get("model") if isinstance(event, dict) else None)
            if m:
                usage_model = m

        def handle(event, force=False) -> bool:
            """Process ONE session event (live stream or replay); True = the turn is over.
            Events carrying a server id are deduped against the per-session ledger, so a
            replayed/overlapping event is processed exactly once. `force` is the
            finished-turn recovery path ONLY (round 7): the tail events are final and
            already ledger-seen, so both the budget check and the dedupe skip are bypassed —
            this is local re-processing of a turn the session completed, not waiting."""
            # BUDGET ON EVERY EVENT (round 6): a BUSY session emitting ordinary events for
            # minutes never hits a requires_action idle or a stream drop, so checking the
            # budget only there let the drain ride the whole turn past the 60s edge ceiling
            # (504) while the held tenant turn lock starved /chat/continue into a 504 too.
            # The moment the per-request budget is spent the turn surfaces UNSETTLED. The
            # check runs BEFORE the dedupe ledger records this event, so the continue leg
            # (fresh budget) re-reads it from events.list and nothing is lost.
            if not force and self._clock() - turn_start >= self._settle_budget_s:
                if not calls and not pending:
                    pending.append({"status": "pending", "reason": "settle_budget"})
                else:
                    pending.extend(calls)
                    calls.clear()
                return True
            eid = getattr(event, "id", None)
            if eid is not None:
                if not force and eid in seen:
                    return False  # already processed (reconnect replay / stream-list overlap)
                seen.add(eid)
            _accumulate_usage(event)  # additive cost observation — dedup-safe (runs post-seen)
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
                # A custom-tool call — the WORKER's to serve, never this process's. Collected
                # as an open call; the entry shape is unchanged from the surface-only adapter
                # so anything that stays unserved surfaces byte-identically.
                calls.append({
                    "status": "pending",
                    "tool": getattr(event, "name", None),
                    "input": getattr(event, "input", None),
                    "custom_tool_use_id": getattr(event, "id", None),
                })
            elif etype == "user.custom_tool_result":
                # The worker answered a call. Close it into the digest; a gated call's
                # pending_approval payload surfaces as the already-routed `tool_name` entry.
                tu_id = (event.get("custom_tool_use_id") if isinstance(event, dict)
                         else getattr(event, "custom_tool_use_id", None))
                idx = next((i for i, entry in enumerate(calls)
                            if entry.get("custom_tool_use_id") == tu_id), None)
                if idx is not None:
                    entry = calls.pop(idx)
                    status, routed = self._digest_tool_result(entry, event)
                    tool_results.append({
                        "tool": entry.get("tool"),
                        "custom_tool_use_id": tu_id,
                        "status": status,
                    })
                    if routed is not None:
                        pending.append(routed)
            elif etype == "session.error":
                errors.append(str(getattr(event, "error", None) or getattr(event, "message", "")))
            elif etype == "session.status_terminated":
                raise RuntimeError(
                    f"MA session {session.id} terminated (irreversible)"
                    + (f" — errors: {errors}" if errors else "")
                )
            elif etype == "session.status_idle":
                # Drain-to-idle gate. "requires_action" with OPEN calls = nothing is serving
                # them (the worker is the only executor and it hasn't answered): surface the
                # open calls as pending and return — fail closed, never execute in-process.
                # The session stays blocked; worker recovery / approval flows resolve it
                # out-of-band. Every other idle ends the turn.
                nonlocal last_stop
                stop = last_stop = self._stop_reason_type(event)
                if stop == "retries_exhausted":
                    raise RuntimeError(
                        f"MA session {session.id} idle after retries_exhausted"
                        + (f" — errors: {errors}" if errors else "")
                    )
                if stop == "requires_action":
                    # SETTLE: requires_action means the session expects EXTERNAL progress —
                    # the worker serving open calls, or a delegated specialist thread whose
                    # tool calls haven't even reached the stream yet (live round-2 finding:
                    # the idle can fire with ZERO open calls while Scout's searches are still
                    # being scheduled). Keep draining within the budget instead of ending the
                    # turn on a race the customer experiences as "I'll report back" + silence.
                    # A routed Greenlight proposal is the one legitimate requires_action stop
                    # (draft-only approval IS the next action) — never wait on it. On budget
                    # exhaustion fail closed exactly as before (worker down / wedged session).
                    routed_stop = any(p.get("tool_name") for p in pending)
                    if not routed_stop and self._clock() - turn_start < self._settle_budget_s:
                        return False
                    if calls:
                        pending.extend(calls)
                        calls.clear()
                    elif not pending:
                        pending.append({"status": "pending", "reason": "requires_action"})
                return True
            return False

        def _finished_tail_recovered() -> bool:
            """ZERO-PROGRESS drop wedge (round 7, live matrix run 2026-06-12): a /chat
            request whose client died (closed tab) keeps draining server-side as an orphan —
            it marks every event seen and its response is lost with the connection. Each
            later continue then replays nothing, reads a silent stream on an already-idle
            session, and surfaces stream_interrupted forever. If the session's event log
            ENDS at a non-requires_action idle the turn is already OVER: force-replay the
            tail (everything after the last user.message) past the dedupe ledger so the
            finished answer and any routed approvals reach THIS request, and settle. A
            requires_action tail keeps the honest unsettled signal — the worker still owes
            a result and the next continue will observe it."""
            try:
                events = list(client.beta.sessions.events.list(
                    session_id=session.id, extra_headers=self._beta_headers()))
            except Exception:
                return False  # can't tell — keep the recoverable unsettled surface
            if not events:
                return False
            tail_stop = events[-1]
            if getattr(tail_stop, "type", None) != "session.status_idle":
                return False
            if self._stop_reason_type(tail_stop) == "requires_action":
                return False
            marks = [i for i, e in enumerate(events)
                     if getattr(e, "type", None) == "user.message"]
            start = (marks[-1] + 1) if marks else 0
            for event in events[start:]:
                if handle(event, force=True):
                    break
            return True

        done = False
        while True:
            # Stream-FIRST, then send: the SSE stream only delivers events emitted after it
            # opens — send-then-stream loses the early events. The same order holds on
            # reconnect: open the fresh stream first, then replay the gap (no second gap).
            with client.beta.sessions.events.stream(
                session_id=session.id, extra_headers=self._beta_headers()
            ) as stream:
                if reconnects == 0 and message is not None:
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
                        # Round 7: a drop with ZERO progress this drain (nothing replayed,
                        # nothing open, no text) on a session whose log ends at a finished
                        # idle is the orphan-consumed wedge — recover the finished turn
                        # instead of surfacing stream_interrupted forever.
                        if (not calls and not pending and not answer_parts
                                and not tool_results and _finished_tail_recovered()):
                            done = True
                            break
                        # Settle guards: budget spent OR reconnects exhausted -> SURFACE the
                        # turn unsettled (round 4) — under the async contract the continue leg
                        # re-attaches and finishes it, so a recoverable in-flight turn must
                        # never become a customer-facing 500 (the pre-continue code raised
                        # here). The interrupted state is marked so the turn reads unsettled
                        # even when no calls/idle were observed yet.
                        if (self._clock() - turn_start >= self._settle_budget_s
                                or reconnects >= MAX_STREAM_RECONNECTS):
                            if not calls and not pending:
                                pending.append(
                                    {"status": "pending", "reason": "stream_interrupted"})
                            break
                        reconnects += 1
                        continue  # re-open the stream, replay the gap, resume the drain
            break
        # Defensive: anything still open at stream end surfaces (never silently dropped).
        pending.extend(calls)
        # A stream that ENDED while we were settle-waiting on a no-calls requires_action keeps
        # the honest blocked-session signal (the pre-settle contract for wedged sessions).
        if not pending and last_stop == "requires_action":
            pending.append({"status": "pending", "reason": "requires_action"})
        return {
            "session_id": session.id,
            "tenant_id": session.tenant_id,
            "delegations": delegations,
            # Paragraph-fold the narration: a settled turn carries the coordinator's interim
            # messages AND the final answer — jammed ""-joins read as one mangled sentence.
            "answer": "\n\n".join(p.strip() for p in answer_parts if p and p.strip()),
            "pending_approvals": pending,
            "tool_results": tool_results,
            # Observed token usage for cost attribution (0/0 when the stream emitted none).
            "usage": {"input_tokens": usage_in, "output_tokens": usage_out, "model": usage_model},
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

    def list_agents(self) -> list[dict[str, Any]]:
        return [
            {
                "id": aid,
                "name": getattr(spec, "name", None),
                "created_at": None,
                "is_coordinator": aid in self.coordinators,
                "agents": list(self.coordinators.get(aid, [])),
            }
            for aid, spec in self.agents.items()
        ]

    def delete_agent(self, agent_id: str) -> None:
        # Idempotent: a double-reap / partial-state delete is a no-op, never a raise.
        self.agents.pop(agent_id, None)
        self.coordinators.pop(agent_id, None)

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

    def resume_session(self, session_id, coordinator_id, tenant_id,
                       vault_id=None, environment_id=None) -> Session:
        return Session(id=session_id, tenant_id=tenant_id, coordinator_id=coordinator_id,
                       metadata={"tenant_id": tenant_id, "environment_id": environment_id})

    def continue_drain(self, session) -> dict:
        # The fake settles every turn in one round — a continue finds nothing in flight.
        return {"session_id": session.id, "tenant_id": session.tenant_id,
                "delegations": [], "answer": "", "pending_approvals": [], "tool_results": []}


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
        # No tool-execution seam here: the deployed EnvironmentWorker is the SINGLE executor of
        # the registry custom tools (docs/decisions/custom-tool-execution-path.md) — this adapter
        # only observes the worker's round-trip on the session event stream.
        return ManagedAgentsRuntime(
            api_key=config.get("api_key"),
            environment_id=config.get("environment_id"),  # the persisted per-tenant env id
            # Per-call-site drain budget: chat keeps the edge-bounded default (None -> env/25s);
            # playbook legs pass their own (scheduled/event 120s, run-now 45s) — the 25s chat
            # default starved the worker's poll+serve+continue cycle on playbook runs (the
            # twice-observed live "incomplete with unserved read-only calls").
            settle_budget_s=config.get("settle_budget_s"),
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
