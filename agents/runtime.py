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
    def create_session(self, coordinator_id: str, tenant_id: str, vault_id: str | None = None) -> Session: ...

    @abc.abstractmethod
    def send_message(self, session: Session, message: str) -> dict[str, Any]: ...


class ManagedAgentsRuntime(AgentRuntime):
    """Real Claude Managed Agents adapter. BETA — every endpoint here is flagged 'verify' and is
    never exercised in tests. The org API key creates sessions/agents; it must never reach the worker.
    """

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key
        self._client = None  # built lazily; import never needs the network

    def _c(self):
        if self._client is None:
            from anthropic import Anthropic  # noqa: PLC0415 — lazy on purpose

            # VERIFY: beta namespace + header shape against the live SDK before use.
            self._client = Anthropic(
                api_key=self._api_key,
                default_headers={"anthropic-beta": MA_BETA_HEADER},
            )
        return self._client

    def create_environment(self, name: str) -> str:
        # VERIFY: POST /v1/environments {"config":{"type":"self_hosted"}}; returns env id.
        raise NotImplementedError("live Anthropic — BLOCKED: needs Nick (creds + beta verify)")

    def create_agent(self, spec) -> str:
        # VERIFY: client.beta.agents.create(name, model, system, tools=[...]).
        raise NotImplementedError("live Anthropic — BLOCKED: needs Nick")

    def create_coordinator(self, spec, agent_ids) -> str:
        # VERIFY: client.beta.agents.create(..., multiagent={"type":"coordinator","agents":[...]}).
        raise NotImplementedError("live Anthropic — BLOCKED: needs Nick")

    def create_vault(self, display_name, external_user_id) -> str:
        # VERIFY: client.beta.vaults.create(display_name, metadata={"external_user_id":...}).
        raise NotImplementedError("live Anthropic — BLOCKED: needs Nick")

    def create_session(self, coordinator_id, tenant_id, vault_id=None) -> Session:
        raise NotImplementedError("live Anthropic — BLOCKED: needs Nick")

    def send_message(self, session, message) -> dict:
        raise NotImplementedError("live Anthropic — BLOCKED: needs Nick")


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

    def create_session(self, coordinator_id, tenant_id, vault_id=None) -> Session:
        s = Session(
            id=self._id("sess"),
            tenant_id=tenant_id,
            coordinator_id=coordinator_id,
            metadata={"tenant_id": tenant_id, "vault_id": vault_id},
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
    """Factory: pick the runtime impl. Defaults to fake unless explicitly configured 'managed'."""
    config = config or {}
    kind = config.get("runtime", "fake")
    if kind == "managed":
        return ManagedAgentsRuntime(api_key=config.get("api_key"))
    if kind == "fake":
        return FakeRuntime()
    raise ValueError(f"unknown runtime: {kind!r}")
