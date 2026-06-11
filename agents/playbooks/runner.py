"""Playbook execution — run an ACTIVATED playbook on a trigger event through the EXISTING plane.

Agent Studio can CRUD + activate playbooks (agents/playbooks/store.py + activation.py), but
activation only REGISTERS a coordinator over the playbook's narrowed roster — nothing ever
*executes* an activated playbook when its trigger fires. This module is that missing seam: the
``PlaybookRunner`` that, given an activated playbook + a trigger event, opens ONE session against
the EXISTING agent runtime (agents/runtime.py), drives the trigger as a coordinator turn, and
returns a run record.

WHAT THE RUNNER NEVER DOES (the same non-negotiables as activation):
  * execute a tool — there is exactly ONE executor of the registry custom tools (the deployed
    EnvironmentWorker on ManagedAgentsRuntime; the runtime's own loop on the HIPAA fallback;
    docs/decisions/custom-tool-execution-path.md). The runner OBSERVES the digest
    (``pending_approvals`` / ``tool_results``) and records it — it never resolves a tool name
    through the registry, never invokes, never default-allows an unknown name;
  * bypass Greenlight — a side-effecting (ALWAYS_ASK) tool's side effect can never run from a
    trigger. The executor builds a Greenlight DRAFT via ``Tool.invoke`` (the Phase 4 base-class
    guarantee); the runner surfaces that proposal as a PROPOSED action and approves NOTHING.
    A real send only happens later, when a human approves the draft through the control plane;
  * widen a grant or honor an autonomy higher than the playbook's — the playbook's autonomy
    level rides into the trigger turn (and, on a live runtime, the worker reads the persisted
    per-tenant dial). ``greenlight.side_effects`` is the schema constant ``always_ask``, so a
    playbook can never grant a send/CRM write autonomy by construction.

The runner is offline-safe: it works against ``FakeRuntime`` and any stub runtime in tests; the
live ``ManagedAgentsRuntime`` path is the same beta/MA-gated seam activation uses, so a runner
that activates a coordinator only does live work where the agent plane is deliberately configured
(CLAUDE.md hard constraint #4). THE TRUST RULE: ``tenant_id`` arrives from the VERIFIED claim /
the scheduler's tenant-scoped trigger record upstream — never read from the event body here.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from agents.playbooks import STATUS_ACTIVE, PlaybookValidationError, validate
from agents.playbooks.activation import activate_playbook


@dataclass
class TriggerEvent:
    """The thing that fired the playbook. ``kind`` mirrors the playbook trigger discriminator
    (``manual`` | ``schedule`` | ``event``); ``name`` is the domain-event/cron label; ``payload``
    is the event body (data only — NEVER a source of tenant identity; see THE TRUST RULE)."""
    kind: str = "manual"
    name: str | None = None
    payload: dict = field(default_factory=dict)

    @classmethod
    def coerce(cls, event: "TriggerEvent | dict | None") -> "TriggerEvent":
        if event is None:
            return cls()
        if isinstance(event, TriggerEvent):
            return event
        if isinstance(event, dict):
            return cls(
                kind=event.get("kind", "manual"),
                name=event.get("name") or event.get("event") or event.get("schedule"),
                payload=event.get("payload") or {},
            )
        raise TypeError(f"unsupported trigger event: {event!r}")


@dataclass
class RunRecord:
    """The result of one playbook run — the audit digest a scheduler/worker persists + surfaces.

    ``status`` is one of:
      * ``ok``        — the trigger turn completed, nothing pending;
      * ``pending``   — the turn proposed side-effecting action(s) for human approval (draft-only);
      * ``not_active``— the playbook exists but isn't activated (no trigger should reach here);
      * ``not_found`` — no such playbook for this tenant (RLS-equivalent: absent);
      * ``error``     — the run failed and was CONTAINED (the trigger source is never crashed).

    ``actions_proposed`` carries the surfaced Greenlight proposals (draft-only; approved NOTHING),
    ``actions_approved`` stays EMPTY by construction (a trigger never auto-approves), ``tool_results``
    the executor-served read-only/gated calls, and ``trace`` an ordered, append-only event log.
    """
    playbook_id: str
    tenant_id: str
    status: str
    trigger: dict = field(default_factory=dict)
    autonomy: str | None = None
    answer: str = ""
    delegations: list[str] = field(default_factory=list)
    actions_proposed: list[dict] = field(default_factory=list)
    actions_approved: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    error: str | None = None
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "playbook_id": self.playbook_id,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "trigger": self.trigger,
            "autonomy": self.autonomy,
            "answer": self.answer,
            "delegations": self.delegations,
            "actions_proposed": self.actions_proposed,
            "actions_approved": self.actions_approved,
            "tool_results": self.tool_results,
            "trace": self.trace,
            "error": self.error,
        }


def _trigger_prompt(definition: dict, event: TriggerEvent) -> str:
    """The coordinator instruction for one trigger turn. Names the playbook, the firing trigger,
    and the autonomy posture — and is EXPLICIT that side-effecting work routes to Greenlight
    (draft-only). The model picks tools; the executor (worker) serves them; the runner observes."""
    name = definition.get("name", "playbook")
    autonomy = definition.get("autonomy", "L1")
    label = event.name or event.kind
    lines = [
        f"Run the '{name}' playbook. It was triggered by {event.kind} ({label}).",
        f"Autonomy level: {autonomy}. Read-only work may auto-run; every side-effecting action "
        "(send/CRM write) MUST route to Greenlight for human approval — never send or mutate "
        "directly.",
    ]
    if event.payload:
        lines.append(f"Trigger payload: {event.payload}")
    if definition.get("description"):
        lines.append(f"Playbook intent: {definition['description']}")
    return "\n".join(lines)


class PlaybookRunner:
    """Connect an activated playbook to its trigger: ``run(tenant_id, playbook_id, event)``.

    Construction is cheap and side-effect-free (no network, no DB). The ``runtime`` is the
    swappable ``AgentRuntime`` adapter — ``FakeRuntime`` / a stub offline, ``ManagedAgentsRuntime``
    live (same beta/MA gate as activation). ``store`` is the tenant-scoped ``PlaybookStore``
    (RLS-scoped reads; THE TRUST RULE binds the tenant upstream). ``environment_id`` / ``vault_id``
    are the playbook's tenant's persisted ids (resolved by the caller); the runner never rebuilds
    them per run.
    """

    def __init__(
        self,
        runtime: Any,
        store: Any,
        *,
        environment_id: str | None = None,
        vault_id: str | None = None,
        run_store: Any | None = None,
    ) -> None:
        self.runtime = runtime
        self.store = store
        self.environment_id = environment_id
        self.vault_id = vault_id
        # PlaybookRunStore-shaped (agents/playbooks/store.py). None -> runs aren't persisted
        # (offline/back-compat); with it, EVERY terminal RunRecord lands as tenant history.
        self.run_store = run_store

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _tail(value: Any) -> str | None:
        """6-char display tail — FULL MA ids are operator material and never reach the trace,
        the wire, or the persisted digest (the api/agents_routes.py contract). The full id
        lives only on the playbook row (the runner needs it to reuse the registration)."""
        if not isinstance(value, str) or not value:
            return None
        return value[-6:]

    def _persist(self, tenant_id: str, record: RunRecord) -> None:
        """Append the terminal digest to the run store — CONTAINED: history is best-effort and
        a persistence failure must never fail (or mask) the run that already happened."""
        if self.run_store is None:
            return
        try:
            self.run_store.record(tenant_id, record.as_dict())
        except Exception:  # noqa: BLE001 — never let history-keeping break the run
            record.trace.append({"event": "run_persist_failed"})

    def _registration_for(self, tenant_id: str, row: dict, record: RunRecord) -> str:
        """The coordinator to run against: REUSE the persisted registration when it matches the
        row's current definition version (the orphan-leak fix — audit P0-3); otherwise register
        through the EXISTING activation mechanism and persist the minted ids for next time."""
        stored = row.get("ma_coordinator_id")
        if stored and row.get("ma_registered_version") == row.get("version"):
            record.trace.append({"event": "reused_registration",
                                 "coordinator_id_tail": self._tail(stored)})
            return stored
        registration = activate_playbook(self.runtime, tenant_id, row["definition"])
        if hasattr(self.store, "set_registration"):
            # Persist the FULL ids on the row (reuse needs them); a store without the seam
            # (older fakes) just re-registers next run — correct, only less efficient.
            # CONTAINED for schema skew (api deployed before the ma_* migrate): persistence
            # failing must never fail the run that is about to happen.
            try:
                self.store.set_registration(
                    tenant_id, row["id"],
                    coordinator_id=registration["coordinator_id"],
                    agent_ids=registration["agent_ids"],
                    version=row.get("version"),
                )
            except Exception:  # noqa: BLE001 — degrade to per-run registration
                record.trace.append({"event": "registration_persist_failed"})
        record.trace.append({"event": "registered",
                             "coordinator_id_tail": self._tail(registration["coordinator_id"]),
                             "agents": registration["agents"]})
        return registration["coordinator_id"]

    @staticmethod
    def _digest(record: RunRecord, resp: dict) -> None:
        """Map ONE ``send_message`` digest onto the run record — OBSERVATION only.

        ``tool_results`` = calls the single executor served this turn (read-only auto-runs +
        gated calls it already routed to Greenlight). ``pending_approvals`` = the surfaced
        Greenlight DRAFTS (already-routed ``tool_name`` entries) plus any call that reached the
        digest UN-served (worker down / unknown tool) — surfaced VERBATIM, never re-invoked,
        never enqueued a second time. NOTHING here approves or executes.
        """
        record.answer = resp.get("answer") or ""
        record.delegations = list(resp.get("delegations") or [])
        record.tool_results = list(resp.get("tool_results") or [])
        record.actions_proposed = list(resp.get("pending_approvals") or [])
        for tr in record.tool_results:
            record.trace.append({"event": "tool_result", "tool": tr.get("tool"),
                                 "status": tr.get("status")})
        for pa in record.actions_proposed:
            if isinstance(pa, dict):
                record.trace.append({
                    "event": "action_proposed",
                    # a routed draft carries `tool_name`; an unserved call carries `tool`
                    "tool": pa.get("tool_name") or pa.get("tool"),
                    "status": pa.get("status"),
                })
        # Draft-only invariant made explicit: a trigger never auto-approves a side effect.
        record.status = "pending" if record.actions_proposed else "ok"

    # ------------------------------------------------------------------ public API
    def run(self, tenant_id: str, playbook_id: str, event: "TriggerEvent | dict | None" = None) -> RunRecord:
        """Run an ACTIVATED playbook for ``tenant_id`` on ``event``; return its ``RunRecord``.

        A failure is CONTAINED: the trigger source (scheduler/worker) calls this and must never
        be crashed by a bad playbook, a down agent plane, or a malformed event — any exception is
        caught and returned as a ``status="error"`` record (the side effects already could not
        have run; nothing partially-executed leaks). Every terminal record is appended to the
        run store (when wired) so the tenant has durable run history (audit P0-2).
        """
        record = self._run(tenant_id, playbook_id, event)
        self._persist(tenant_id, record)
        return record

    def _run(self, tenant_id: str, playbook_id: str, event: "TriggerEvent | dict | None") -> RunRecord:
        ev = TriggerEvent.coerce(event)
        trigger = {"kind": ev.kind, "name": ev.name}
        try:
            row = self.store.get(tenant_id, playbook_id)
            if row is None:
                return RunRecord(playbook_id=str(playbook_id), tenant_id=str(tenant_id),
                                 status="not_found", trigger=trigger,
                                 trace=[{"event": "lookup", "result": "absent"}])
            if row.get("status") != STATUS_ACTIVE:
                return RunRecord(playbook_id=str(playbook_id), tenant_id=str(tenant_id),
                                 status="not_active", trigger=trigger,
                                 trace=[{"event": "lookup", "result": "not_active"}])

            definition = row["definition"]
            # Defense in depth (mirrors activation): a stored row edited out-of-band, or a schema
            # tightening since it activated, must fail BEFORE anything registers or runs.
            validate(definition)

            record = RunRecord(
                playbook_id=str(row["id"]), tenant_id=str(tenant_id), status="ok",
                trigger=trigger, autonomy=definition.get("autonomy"),
            )
            record.trace.append({"event": "triggered", "kind": ev.kind, "name": ev.name})

            # REUSE the persisted MA registration when fresh; register + persist otherwise
            # (the EXISTING activation mechanism — tools come from the trusted registry, so
            # side-effecting members stay ALWAYS_ASK / Greenlight-drafted regardless of the JSON).
            coordinator_id = self._registration_for(tenant_id, row, record)

            # ONE session per run, bound to THIS tenant's persisted environment (per-tenant,
            # never instance-global). The tenant in session metadata is what the worker pushes
            # into app.current_tenant (RLS) when it serves a tool.
            session = self.runtime.create_session(
                coordinator_id, tenant_id=tenant_id,
                vault_id=self.vault_id, environment_id=self.environment_id,
            )
            record.trace.append({"event": "session", "session_id_tail": self._tail(session.id)})

            resp = self.runtime.send_message(session, _trigger_prompt(definition, ev))
            self._digest(record, resp)
            return record
        except PlaybookValidationError as exc:
            return RunRecord(playbook_id=str(playbook_id), tenant_id=str(tenant_id),
                             status="error", trigger=trigger, error=f"invalid playbook: {exc}",
                             trace=[{"event": "error", "detail": str(exc)}])
        except Exception as exc:  # noqa: BLE001 — contain ANY failure; never crash the trigger source
            return RunRecord(playbook_id=str(playbook_id), tenant_id=str(tenant_id),
                             status="error", trigger=trigger, error=str(exc),
                             trace=[{"event": "error", "detail": str(exc)}])


def run(runtime: Any, store: Any, tenant_id: str, playbook_id: str,
        event: "TriggerEvent | dict | None" = None, *,
        environment_id: str | None = None, vault_id: str | None = None,
        run_store: Any | None = None) -> RunRecord:
    """Module-level convenience seam — the single entry a scheduler/worker calls when a trigger
    fires: build a ``PlaybookRunner`` and run one playbook. Stays behind the SAME beta/MA-gated
    seam as activation (the live registrar is owner-gated; offline it runs against FakeRuntime).
    """
    return PlaybookRunner(
        runtime, store, environment_id=environment_id, vault_id=vault_id, run_store=run_store,
    ).run(tenant_id, playbook_id, event)
