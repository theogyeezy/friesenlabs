"""The HIPAA fallback runtime (TODO AI/P3): a direct Anthropic Messages tool-use loop.

WHY THIS EXISTS — the tenancy model (CLAUDE.md) routes HIPAA tenants OFF Managed Agents "via the
runtime.py seam", and TODO.md's open question settles what that path can actually be: Managed
Agents does NOT exist on Amazon Bedrock (MA is first-party API + Claude Platform on AWS only, and
Claude-Platform-on-AWS does not support self_hosted sandboxes). So the documented HIPAA path is
this fallback: the SAME `AgentRuntime` interface implemented as a client-side Messages API
tool-use loop — no MA sessions, no Anthropic-side environment, no Anthropic-side state beyond
the model call itself.

What stays IDENTICAL to the managed plane (the control invariants):
  - the SAME trusted registry tools (`agents/tools/registry.py`) — an unknown tool name is never
    default-allowed: the loop refuses it, tells the model, and surfaces the event;
  - the SAME Greenlight ALWAYS_ASK routing — a side-effecting tool is invoked through
    `Tool.invoke`, whose Phase 4 base class builds a PROPOSAL and routes it to Greenlight
    WITHOUT performing the side effect (draft-only stays guaranteed at the base class);
  - THE TRUST RULE — tenant_id enters at `create_session(...)` from the caller (the verified
    Cognito claim upstream) and rides `Session` metadata into every ToolContext; nothing here
    reads it from env, headers, or payloads.

What is intentionally DIFFERENT:
  - `create_environment` / `create_agent` / `create_coordinator` / `create_vault` create no
    Anthropic-side resources. They return SYNTHETIC local ids ("selfhosted-…"), persisted
    per-tenant via the injected WorkspaceStore (the same `tenant_workspaces` row the managed
    path uses) so provisioning and the conversation factory work unchanged. The roster itself
    is CODE (`agents/roster`), so a fresh process needs no Anthropic-side lookup to serve a
    persisted coordinator id.
  - there are no subagent threads — the loop is a single coordinator-persona model running the
    registry's tool surface directly, so the digest's `delegations` is always [].
  - tools execute IN-PROCESS: this process IS the VPC-side executor. There is no environment
    worker, no work queue, and no environment key.

`send_message` returns the SAME digest shape as `ManagedAgentsRuntime.send_message`
({session_id, tenant_id, delegations, answer, pending_approvals}), with one deliberate
difference in the pending entries: this runtime has ALREADY routed side-effecting calls
through Greenlight, so its entries carry `tool_name` (NOT `tool`) — `conv.session.Conversation`
resolves-and-invokes only entries carrying `tool`, so already-routed entries pass through
untouched and a proposal is never enqueued twice.

No MA beta header here: this is a plain `/v1/messages` surface (the repo's "MA header on every
Anthropic call" convention covers the agent plane's `client.beta.*` namespaces — same reasoning
as `conv/synthesizer.py`).

Import-safe: the `anthropic` SDK loads lazily on first use; constructing the class needs no
network and no creds. Tests inject a mocked `client`.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Callable

from .coordinator import COORDINATOR
from .runtime import (
    DELEGATION_DEPTH,
    MAX_AGENTS_PER_ROSTER,
    AgentRuntime,
    Session,
)
from .tools.base import ToolContext
from .tools.registry import get_tool

# Bounded loop defaults. max_turns counts MODEL CALLS (each may carry several parallel tool
# calls); the bound failing loudly beats silently truncating a half-finished action sequence.
DEFAULT_MAX_TURNS = 8
# Non-streaming default per the claude-api guidance (don't lowball max_tokens; ~16K stays under
# SDK HTTP timeouts without streaming).
DEFAULT_MAX_TOKENS = 16000

_LOOP_SYSTEM_SUFFIX = (
    "\n\nRUNTIME NOTE: you are running on the self-hosted compliance runtime. You have NO "
    "subagents — do the work yourself with the tools provided. Side-effecting tools "
    "(send_email, update_deal, issue_quote) never execute directly: calling one routes a "
    "proposal to a human approval queue (Greenlight) and returns immediately — report that the "
    "action is awaiting human approval instead of retrying it."
)


def _loop_tool_specs() -> list[dict]:
    """Every trusted registry tool, serialized to the plain Messages-API client-side tool shape
    ({name, description, input_schema} — NOT `Tool.to_spec()`, whose `{"type": "custom"}` wrapper
    is the Managed Agents custom-tool shape)."""
    from .tools.registry import TOOL_REGISTRY  # noqa: PLC0415 — single source of truth

    return [
        {"name": cls.name, "description": cls.description, "input_schema": cls.input_schema}
        for cls in TOOL_REGISTRY.values()
    ]


class SelfHostedToolUseRuntime(AgentRuntime):
    """HIPAA fallback `AgentRuntime`: a bounded Messages-API tool-use loop over the trusted
    registry, with the standard Greenlight ALWAYS_ASK routing.

    Injection points:
      - `workspace_store` + `tenant_id`: when both are given (the per-tenant provisioning
        posture, mirroring `signup/provisioning.py`), every synthetic id minted by `create_*`
        is merge-upserted into THIS tenant's `tenant_workspaces` row, so the ids survive the
        process. `tenant_id` here is the provisioning-time tenant (verified upstream in the
        signup flow) — the REQUEST-path tenant still arrives per session via `create_session`.
      - `greenlight`: the approval queue ALWAYS_ASK proposals land in (default ToolContext).
      - `tool_context_factory(session) -> ToolContext`: full client wiring (db/rag/cube/cortex),
        mirroring `worker.build_context`. The default context carries tenant + greenlight only,
        so read tools degrade to empty results and side-effecting tools still only propose.
      - `client_factory`: builds the Anthropic client — the Bedrock-vs-first-party seam (see
        the VERIFY note in `_c`).
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        workspace_store: Any = None,
        tenant_id: str | None = None,
        greenlight: Any = None,
        tool_context_factory: Callable[[Session], ToolContext] | None = None,
        model: str | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client_factory: Callable[[], Any] | None = None,
        agent_name: str = "uplift-orchestrator",
    ) -> None:
        if max_turns < 1:
            raise ValueError(f"max_turns must be >= 1, got {max_turns}")
        self._api_key = api_key
        self._client = None  # built lazily; import/construction never touch the network
        self._client_factory = client_factory
        self._store = workspace_store
        self._tenant_id = tenant_id
        self._greenlight = greenlight
        self._tool_context_factory = tool_context_factory
        self._model = model
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self._agent_name = agent_name
        # Local definition registries (the "Anthropic-side" state the managed plane would hold).
        self._agent_specs: dict[str, Any] = {}
        self._coordinator_specs: dict[str, Any] = {}
        self._coordinator_rosters: dict[str, list[str]] = {}
        self._sessions: dict[str, Session] = {}
        self._histories: dict[str, list[dict]] = {}

    # ------------------------------------------------------------------ client
    def _c(self):
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                # VERIFY (Bedrock-vs-1P — the load-bearing HIPAA endpoint choice): this default
                # is the FIRST-PARTY Anthropic API (api.anthropic.com) — correct when the
                # tenant's BAA is with Anthropic. A tenant whose compliance posture requires
                # AWS-side inference must run this SAME loop over Amazon Bedrock instead:
                # `anthropic.AnthropicBedrock` (SigV4 auth, no api_key) and the bare roster
                # model ids gain the "anthropic." prefix (e.g. "anthropic.claude-opus-4-8").
                # Managed Agents and server-side tools do not exist on Bedrock — which is
                # exactly why this loop uses client-side custom tools only. Confirm WHICH
                # endpoint each HIPAA tenant is contracted for BEFORE onboarding, and inject
                # the matching client via `client_factory` (plus the prefixed `model`).
                from anthropic import Anthropic  # noqa: PLC0415 — lazy on purpose

                self._client = Anthropic(api_key=self._api_key)
        return self._client

    # ------------------------------------------------------------------ persistence
    @staticmethod
    def _id(prefix: str) -> str:
        return f"selfhosted-{prefix}-{uuid.uuid4().hex[:12]}"

    def _persist(self, **fields: str) -> None:
        """Merge-upsert THIS tenant's `tenant_workspaces` row. The synthetic ids are local-only
        until persisted; the row is what makes them durable for the conversation factory."""
        if self._store is None or self._tenant_id is None:
            return
        row = self._store.get(self._tenant_id) or {}
        merged = {
            "workspace_id": row.get("workspace_id"),
            "environment_id": row.get("environment_id"),
            "coordinator_id": row.get("coordinator_id"),
        }
        merged.update(fields)
        self._store.upsert(
            self._tenant_id,
            merged["workspace_id"],
            merged["environment_id"],
            merged["coordinator_id"],
        )

    # ------------------------------------------------------------------ create_* (synthetic)
    def create_environment(self, name: str) -> str:
        # No self-hosted MA sandbox exists here — the "environment" is a local marker only;
        # tools execute in-process in our VPC. Persisted so `coordinator.build` + the
        # conversation factory see a complete row.
        eid = self._id("env")
        self._persist(environment_id=eid)
        return eid

    def create_agent(self, spec) -> str:
        aid = self._id("agent")
        self._agent_specs[aid] = spec
        return aid

    def create_coordinator(self, spec, agent_ids) -> str:
        agent_ids = list(agent_ids)
        # Keep the managed plane's hard limits for parity: a roster authored against MA must not
        # silently exceed them on the fallback (the two runtimes stay swap-compatible).
        if len(agent_ids) > MAX_AGENTS_PER_ROSTER:
            raise ValueError(
                f"roster of {len(agent_ids)} agents exceeds the limit of {MAX_AGENTS_PER_ROSTER}"
            )
        nested = sorted(a for a in agent_ids if a in self._coordinator_specs)
        if nested:
            raise ValueError(
                f"delegation depth is {DELEGATION_DEPTH} (flat topology): coordinators cannot "
                f"delegate to other coordinators: {nested}"
            )
        cid = self._id("coord")
        self._coordinator_specs[cid] = spec
        self._coordinator_rosters[cid] = agent_ids
        self._persist(coordinator_id=cid)
        return cid

    def create_vault(self, display_name, external_user_id) -> str:
        # Vaults are an MA isolation concept; on this runtime the isolation boundary is Postgres
        # RLS + the per-tenant ToolContext. The synthetic id keeps the interface (and
        # `create_session(vault_id=...)` callers) working unchanged.
        return self._id("vault")

    def create_session(self, coordinator_id, tenant_id, vault_id=None, environment_id=None) -> Session:
        # THE TRUST RULE: tenant_id arrives from the caller (verified-claim upstream) — it rides
        # the Session metadata into every per-call ToolContext, never read from env here.
        s = Session(
            id=self._id("sess"),
            tenant_id=tenant_id,
            coordinator_id=coordinator_id,
            metadata={"tenant_id": tenant_id, "vault_id": vault_id},
        )
        self._sessions[s.id] = s
        self._histories[s.id] = []
        return s

    # ------------------------------------------------------------------ the tool-use loop
    def _default_tool_context(self, session: Session) -> ToolContext:
        # Minimal context: tenant (trust rule) + Greenlight. Read tools degrade to empty
        # results; ALWAYS_ASK tools still only propose (base-class guarantee, with or without
        # a configured Greenlight).
        return ToolContext(
            tenant_id=session.metadata["tenant_id"],
            agent=self._agent_name,
            greenlight=self._greenlight,
        )

    def _system_prompt(self, session: Session) -> str:
        spec = self._coordinator_specs.get(session.coordinator_id)
        base = getattr(spec, "system", None) or COORDINATOR.system
        return base + _LOOP_SYSTEM_SUFFIX

    def _model_for(self, session: Session) -> str:
        if self._model:
            return self._model
        spec = self._coordinator_specs.get(session.coordinator_id)
        # Bare first-party model ids (roster). On a Bedrock client these need the "anthropic."
        # prefix — inject `model` alongside `client_factory` (see the VERIFY note in _c).
        return getattr(spec, "model", None) or COORDINATOR.model

    @staticmethod
    def _digest_content(resp: Any) -> tuple[list[dict], list[dict]]:
        """Normalize a Messages response's content blocks to plain dicts for the history, and
        split out the tool_use blocks. Thinking is intentionally NOT enabled in this loop (a
        replayed thinking block needs its signature; the fallback loop stays simple), so only
        text/tool_use blocks are kept."""
        content: list[dict] = []
        tool_uses: list[dict] = []
        for block in getattr(resp, "content", None) or []:
            if isinstance(block, dict):
                btype = block.get("type")
                get = block.get
            else:
                btype = getattr(block, "type", None)
                get = lambda k, _b=block: getattr(_b, k, None)  # noqa: E731
            if btype == "text":
                content.append({"type": "text", "text": get("text") or ""})
            elif btype == "tool_use":
                tu = {
                    "type": "tool_use",
                    "id": get("id"),
                    "name": get("name"),
                    "input": get("input") or {},
                }
                content.append(tu)
                tool_uses.append(tu)
        return content, tool_uses

    def _run_tool(self, ctx: ToolContext, tool_use: dict, pending: list[dict]) -> dict:
        """Execute ONE model-requested tool call; return the tool_result block to feed back.

        - unknown name: never default-allowed — refused with is_error, surfaced in `pending`;
        - ALWAYS_ASK: `Tool.invoke` routes the proposal to Greenlight (NEVER the side effect);
          surfaced in `pending` as an already-routed entry (`tool_name`, not `tool` — see the
          module docstring) and reported to the model as queued-for-approval;
        - AUTO: executed in-process, result fed back.
        """
        name, tu_id = tool_use["name"], tool_use["id"]
        tool_cls = get_tool(name)
        if tool_cls is None:
            pending.append({"status": "unknown_tool", "tool_name": name,
                            "custom_tool_use_id": tu_id})
            return {
                "type": "tool_result", "tool_use_id": tu_id, "is_error": True,
                "content": f"unknown tool {name!r}: not in the trusted registry; refusing to "
                           "execute (tools are never default-allowed)",
            }
        try:
            out = tool_cls().invoke(ctx, **(tool_use["input"] or {}))
        except Exception as exc:  # bad model input / degraded client — keep the loop alive
            return {
                "type": "tool_result", "tool_use_id": tu_id, "is_error": True,
                "content": f"{name} failed: {exc}",
            }
        if out.get("status") == "pending_approval":
            entry: dict = {
                "status": "pending_approval",
                "tool_name": name,  # NOT "tool": already routed — Conversation must not re-invoke
                "custom_tool_use_id": tu_id,
                "proposal": out.get("proposal"),
            }
            if out.get("approval") is not None:
                entry["approval"] = out["approval"]
            if out.get("greenlight") == "unconfigured":
                entry["greenlight"] = "unconfigured"
            pending.append(entry)
            return {
                "type": "tool_result", "tool_use_id": tu_id,
                "content": json.dumps({
                    "status": "pending_approval",
                    "detail": f"{name} is side-effecting: a proposal was routed to the "
                              "Greenlight approval queue for a human decision; the action was "
                              "NOT performed.",
                }),
            }
        return {
            "type": "tool_result", "tool_use_id": tu_id,
            "content": json.dumps(out.get("result"), default=str),
        }

    def send_message(self, session: Session, message: str) -> dict[str, Any]:
        """One user turn through the bounded tool-use loop.

        Returns the ManagedAgentsRuntime-compatible digest
        {session_id, tenant_id, delegations, answer, pending_approvals}; `delegations` is
        always [] (no subagent threads here). Raises RuntimeError if the model still wants
        tools after `max_turns` model calls — fail loud, never silently truncate.
        """
        history = self._histories.setdefault(session.id, [])
        history.append({"role": "user", "content": [{"type": "text", "text": message}]})

        client = self._c()
        ctx_factory = self._tool_context_factory or self._default_tool_context
        tools = _loop_tool_specs()
        system = self._system_prompt(session)
        model = self._model_for(session)

        answer_parts: list[str] = []
        pending: list[dict] = []
        for _ in range(self.max_turns):
            resp = client.messages.create(
                model=model,
                max_tokens=self.max_tokens,
                system=system,
                tools=tools,
                messages=list(history),
            )
            assistant_content, tool_uses = self._digest_content(resp)
            history.append({"role": "assistant", "content": assistant_content})
            answer_parts.extend(
                b["text"] for b in assistant_content if b.get("type") == "text" and b.get("text")
            )
            if not tool_uses:
                # end_turn / max_tokens / refusal — either way the model stopped calling tools.
                return {
                    "session_id": session.id,
                    "tenant_id": session.tenant_id,
                    "delegations": [],  # single-model loop: no subagent threads
                    "answer": "\n".join(answer_parts),
                    "pending_approvals": pending,
                }
            # Fresh ToolContext per model turn (fresh extra dict; never share mutable state).
            ctx = ctx_factory(session)
            results = [self._run_tool(ctx, tu, pending) for tu in tool_uses]
            history.append({"role": "user", "content": results})
        raise RuntimeError(
            f"self-hosted tool-use loop did not settle within max_turns={self.max_turns} for "
            f"session {session.id} — refusing to continue (raise max_turns deliberately if the "
            "task legitimately needs more tool calls)"
        )
