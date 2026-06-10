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
from dataclasses import dataclass, field
from typing import Any

from shared.config import MA_BETA_HEADER

# Hard multi-agent limits (Build Guide Step 24, "THE HARD MULTI-AGENT LIMITS").
DELEGATION_DEPTH = 1          # no nested sub-teams
MAX_AGENTS_PER_ROSTER = 20
MAX_CONCURRENT_THREADS = 25

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
    """

    def __init__(self, api_key: str | None = None, environment_id: str | None = None):
        self._api_key = api_key
        self._client = None  # built lazily; import never needs the network
        self._environment_id = environment_id
        self._coordinator_ids: set[str] = set()  # coordinators created here (depth-1 guard)
        self._session_ids: set[str] = set()      # sessions opened here (thread-cap guard)

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
        stream-first -> send -> drain-to-idle (the MA-readiness rework in TODO.md).

        Returns the FakeRuntime-compatible shape {session_id, tenant_id, delegations, answer}
        plus `pending_approvals` for any client-side tool calls surfaced during the turn.
        """
        client = self._c()
        answer_parts: list[str] = []
        delegations: list[str] = []
        pending: list[dict] = []
        errors: list[str] = []
        # Stream-FIRST, then send: the SSE stream only delivers events emitted after it opens —
        # send-then-stream loses the early events.
        with client.beta.sessions.events.stream(
            session_id=session.id, extra_headers=self._beta_headers()
        ) as stream:
            client.beta.sessions.events.send(
                session_id=session.id,
                events=[{"type": "user.message", "content": [{"type": "text", "text": message}]}],
                extra_headers=self._beta_headers(),
            )
            for event in stream:
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
                    # A client-side tool call. NEVER executed here — surfaced as a pending action;
                    # side-effecting tools route through Greenlight via the Phase 4 Tool base class
                    # in whatever executor resolves this (draft-only stays guaranteed).
                    pending.append(
                        {
                            "status": "pending",
                            "tool": getattr(event, "name", None),
                            "input": getattr(event, "input", None),
                            "custom_tool_use_id": getattr(event, "id", None),
                        }
                    )
                elif etype == "session.error":
                    errors.append(str(getattr(event, "error", None) or getattr(event, "message", "")))
                elif etype == "session.status_terminated":
                    raise RuntimeError(
                        f"MA session {session.id} terminated (irreversible)"
                        + (f" — errors: {errors}" if errors else "")
                    )
                elif etype == "session.status_idle":
                    # Drain-to-idle gate. We break on EVERY idle (never spin on the stream):
                    # "requires_action" means the session is blocked on a client-side event (tool
                    # confirmation / custom tool result) — this adapter holds no tool executor, so
                    # the blocked state is surfaced as pending rather than awaited forever.
                    # VERIFY: whether self-hosted environment-worker tool execution ever surfaces as
                    # requires_action here, or stays "running" while the worker drains the queue.
                    stop = self._stop_reason_type(event)
                    if stop == "retries_exhausted":
                        raise RuntimeError(
                            f"MA session {session.id} idle after retries_exhausted"
                            + (f" — errors: {errors}" if errors else "")
                        )
                    if stop == "requires_action" and not pending:
                        pending.append({"status": "pending", "reason": "requires_action"})
                    break
        return {
            "session_id": session.id,
            "tenant_id": session.tenant_id,
            "delegations": delegations,
            "answer": "".join(answer_parts),
            "pending_approvals": pending,
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
