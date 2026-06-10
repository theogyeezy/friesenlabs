#!/usr/bin/env python3
"""verify_agent_plane.py — the live end-to-end agent-plane smoke (TODO AI/P1).

WHAT THIS PROVES (one structured PASS/FAIL line per step):

  [1] workspace   provision-or-load the test tenant's `tenant_workspaces` row (the persisted
                  Managed Agents ids: environment_id + coordinator_id, non-stub)
  [2] chat        one live coordinator round-trip over an MA session — non-empty answer,
                  delegations recorded
  [3] grounding   a grounded RAG answer with >= 1 verified citation (pgvector retrieval +
                  AnthropicSynthesizer; the "no uncited claim" invariant) — needs the DB
  [4] greenlight  a side-effecting action (send_email) runs the ActionGate and lands in
                  Greenlight as PENDING — the executor is NOT called, NO send happens,
                  exactly one `pending_approval` trace is written
  [5] approve     a human-style approve flips the record to `approved`
  [6] execute     the approved action dispatches through the SAME gate + executor seam the API
                  wires (api/asgi.make_executor shape): executor called exactly once, EXACTLY
                  ONE `executed` trace written — and the draft-only guarantee holds (the
                  "executed" send_email still only produces a Greenlight proposal; no real
                  email leaves — ALLOW_REAL_SENDS stands)

SAFE BY DEFAULT — offline PLAN mode:
  Unless UPLIFT_LIVE_VERIFY is exactly "true"/"1" AND every required credential is present,
  NO live call is made: the script prints exactly what each step WOULD do, names anything
  missing, and exits 0. Importing this module has no side effects, so offline CI can import
  or run it harmlessly.

RUN (Lane Nick — after the MA env exists; see infra/RUNBOOK.md "AI-plane gate flipped"):

    export UPLIFT_LIVE_VERIFY=1
    export ANTHROPIC_API_KEY=sk-ant-...        # org key (Secrets Manager: uplift/anthropic-api-key)
    export UPLIFT_ENV_ID=env_...               # the MA self-hosted environment id (uplift/env-id)
    export UPLIFT_VERIFY_TENANT_ID=<uuid>      # the TEST tenant (e.g. the seeded demo tenant's
                                               #   custom:tenant_id — see uplift/demo-user)
    # Optional — the full data-plane legs (workspace row in Aurora, RAG citations, Pg-backed
    # Greenlight). Without a DSN the script uses in-memory stores: steps 4-6 still prove the
    # gate/queue/trace invariants, step 1 needs UPLIFT_VERIFY_ALLOW_PROVISION, step 3 SKIPs.
    export UPLIFT_DB_URL=postgresql://crm_app:...@<aurora-endpoint>:5432/uplift
    #   (or the discrete DB_USER/DB_PASS/DB_HOST/DB_NAME parts — shared/config.dsn_from_env)
    # Optional — allow provisioning when the tenant has no workspace row yet. This CREATES REAL
    # Anthropic resources (7 specialists + 1 coordinator) in the EXISTING environment above
    # (never a new environment) and persists the ids. Off by default.
    export UPLIFT_VERIFY_ALLOW_PROVISION=1
    # Optional — the draft email recipient for steps 4-6 (default approvals-demo@example.com;
    # nothing sends either way — the draft gate stands).
    export UPLIFT_VERIFY_EMAIL_TO=you@example.com

    python scripts/verify_agent_plane.py        # exit 0 = all steps passed (or PLAN mode)
                                                # exit 1 = at least one step FAILED

  In-VPC: Aurora is private — run this as a one-off Fargate task cloned from the live
  uplift-api task def (the scripts/seed_demo_tenant.py / api.migrate pattern: same image,
  command override `python scripts/verify_agent_plane.py`, env injected from Secrets Manager).
  The org key belongs to the API task posture ONLY — never bake these env vars into the worker
  task definition.

NOTES / HONEST LIMITS:
  - Step 2 exercises the live MA session (stream-first send via agents/runtime.py). Whether the
    coordinator chooses to call tools on this turn is model behavior — step 2 only asserts the
    round-trip; the deterministic Greenlight proof is step 4.
  - Step 6 models the post-approval dispatch in-process: the repo has no post-approval execution
    endpoint yet (the gate executes only Decision.AUTO), so the human grant is represented as a
    per-tenant L3 autonomy override and the SAME approved action is re-dispatched through the
    SAME ActionGate + registry executor. # VERIFY: the production post-approval path (the MA
    `user.tool_confirmation` reply mapped by Greenlight.to_ma_confirmation) is still TODO and is
    NOT exercised here.
  - Cleanup: every approval this script created is denied at the end (best effort) so the live
    queue is left empty.
THE TRUST RULE (script posture): the tenant id comes from UPLIFT_VERIFY_TENANT_ID, supplied by
the operator running this verify — it stands in for the verified-claim parameter the API would
thread. It is never read from request-shaped input, and nothing here weakens the API's own
claim-only binding.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

# Runnable both as `python scripts/verify_agent_plane.py` and from the repo root.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.config import (  # noqa: E402
    ENV_ANTHROPIC_API_KEY,
    ENV_UPLIFT_ENV_ID,
    ENV_UPLIFT_LIVE_VERIFY,
    ENV_UPLIFT_VERIFY_ALLOW_PROVISION,
    ENV_UPLIFT_VERIFY_EMAIL_TO,
    ENV_UPLIFT_VERIFY_TENANT_ID,
    _switch_env,
    dsn_from_env,
)

DEFAULT_QUESTION = "Which of our open deals look most at risk this quarter, and why?"
DEFAULT_EMAIL_TO = "approvals-demo@example.com"
# CAN-SPAM: the compliance validator blocks an email draft without an unsubscribe mechanism —
# the verify draft carries one so the gate exercises Greenlight, not the compliance block.
DRAFT_BODY = (
    "[uplift verify_agent_plane] This is a draft created by the live agent-plane smoke. "
    "It is never sent (draft gate). Reply 'unsubscribe' to opt out."
)

STEPS = [
    ("workspace", "provision-or-load the tenant's tenant_workspaces row (persisted MA ids)"),
    ("chat", "live coordinator round-trip over an MA session -> non-empty answer"),
    ("grounding", "grounded RAG answer with >= 1 verified citation (pgvector + synthesizer)"),
    ("greenlight", "send_email through the ActionGate -> PENDING approval, no execution, 1 trace"),
    ("approve", "human-style approve flips the record to approved"),
    ("execute", "approved action dispatches via gate+executor -> exactly 1 'executed' trace, draft-only"),
]


class Report:
    """Collects structured step results and prints them as they land."""

    def __init__(self):
        self.results: list[dict] = []

    def step(self, name: str, status: str, detail: str = "") -> None:
        self.results.append({"step": name, "status": status, "detail": detail})
        print(f"[{status:>4}] {name:<10} {detail}")

    @property
    def failed(self) -> bool:
        return any(r["status"] == "FAIL" for r in self.results)

    def summary(self) -> dict:
        return {
            "ok": not self.failed,
            "steps": self.results,
        }


def _required_env() -> tuple[dict, list[str]]:
    """Resolve the live-mode env contract; return (values, missing-names)."""
    wanted = {
        "api_key": ENV_ANTHROPIC_API_KEY,
        "env_id": ENV_UPLIFT_ENV_ID,
        "tenant_id": ENV_UPLIFT_VERIFY_TENANT_ID,
    }
    values = {k: os.environ.get(name, "") for k, name in wanted.items()}
    missing = [name for k, name in wanted.items() if not values[k]]
    return values, missing


def _print_plan(missing: list[str], live_flag: bool) -> None:
    reason = (
        f"{ENV_UPLIFT_LIVE_VERIFY} is not 'true'/'1'" if not live_flag
        else f"missing required env: {', '.join(missing)}"
    )
    print(f"verify_agent_plane: OFFLINE PLAN MODE ({reason}) — no live calls will be made.\n")
    for name, what in STEPS:
        print(f"[PLAN] {name:<10} would: {what}")
    print(
        "\nTo run live: set UPLIFT_LIVE_VERIFY=1 plus "
        f"{ENV_ANTHROPIC_API_KEY}, {ENV_UPLIFT_ENV_ID}, {ENV_UPLIFT_VERIFY_TENANT_ID} "
        "(and optionally a crm_app DSN + UPLIFT_VERIFY_ALLOW_PROVISION). "
        "See the module docstring for the full runbook."
    )


# --------------------------------------------------------------------------- live steps
def _build_stores(dsn: str | None):
    """Workspace store + Greenlight + tool clients: Aurora-backed when a DSN is configured,
    in-memory otherwise (agent-plane-only posture)."""
    if dsn:
        from agents.workspace_store import PgWorkspaceStore
        from api.control.greenlight import Greenlight, PgApprovalStore
        from api.pg_clients import PgCrmClient, PgRagClient

        return {
            "workspace_store": PgWorkspaceStore(dsn),
            "greenlight": Greenlight(store=PgApprovalStore(dsn)),
            "crm": PgCrmClient(dsn),
            "rag": PgRagClient(dsn),
        }
    from agents.workspace_store import InMemoryWorkspaceStore
    from api.control.greenlight import Greenlight

    return {
        "workspace_store": InMemoryWorkspaceStore(),
        "greenlight": Greenlight(),
        "crm": None,
        "rag": None,
    }


def _step_workspace(report: Report, stores: dict, env: dict) -> dict | None:
    """[1] Load the tenant's persisted MA ids; provision them (in the EXISTING environment)
    only under the explicit allow-provision switch."""
    tenant_id = env["tenant_id"]
    store = stores["workspace_store"]
    row = store.get(tenant_id)
    complete = bool(row and row.get("environment_id") and row.get("coordinator_id"))
    stubby = bool(row) and any(
        isinstance(v, str) and v.startswith("stub-")
        for v in (row or {}).values()
    )
    if complete and not stubby:
        report.step("workspace", "PASS",
                    f"loaded persisted row (coordinator={row['coordinator_id']}, "
                    f"environment={row['environment_id']})")
        return row
    if stubby:
        report.step("workspace", "FAIL",
                    "row holds offline 'stub-' placeholder ids — re-provision this tenant "
                    "against live Managed Agents before verifying")
        return None
    if not _switch_env(ENV_UPLIFT_VERIFY_ALLOW_PROVISION):
        report.step("workspace", "FAIL",
                    f"no complete tenant_workspaces row for {tenant_id} and "
                    f"{ENV_UPLIFT_VERIFY_ALLOW_PROVISION} is off — set it to 1 to let this "
                    "script create the roster (7 specialists + coordinator) in the existing "
                    f"environment {env['env_id']}")
        return None

    # Provision: agents + coordinator in the EXISTING environment (never create a new one —
    # the runtime is constructed bound to env_id, and create_environment would refuse anyway).
    from agents.coordinator import COORDINATOR
    from agents.roster import roster
    from agents.runtime import get_runtime

    runtime = get_runtime({"runtime": "managed", "api_key": env["api_key"],
                           "environment_id": env["env_id"]})
    agent_ids = [runtime.create_agent(spec) for spec in roster()]
    coordinator_id = runtime.create_coordinator(COORDINATOR, agent_ids)
    store.upsert(tenant_id, (row or {}).get("workspace_id"), env["env_id"], coordinator_id)
    row = store.get(tenant_id)
    report.step("workspace", "PASS",
                f"provisioned {len(agent_ids)} specialists + coordinator "
                f"{coordinator_id} in {env['env_id']}; row persisted")
    return row


def _step_chat(report: Report, stores: dict, env: dict, row: dict):
    """[2] One live coordinator round-trip via conv.session.Conversation."""
    from agents.runtime import get_runtime
    from conv.session import Conversation
    from conv.synthesizer import AnthropicSynthesizer

    runtime = get_runtime({"runtime": "managed", "api_key": env["api_key"],
                           "environment_id": row["environment_id"]})
    crm = stores["crm"]
    convo = Conversation(
        tenant_id=env["tenant_id"],
        today=date.today(),
        runtime=runtime,
        coordinator_id=row["coordinator_id"],
        environment_id=row["environment_id"],
        rag=stores["rag"],
        crm=crm.for_tenant(env["tenant_id"]) if hasattr(crm, "for_tenant") else crm,
        synthesizer=AnthropicSynthesizer(api_key=env["api_key"]),
        greenlight=stores["greenlight"],
    )
    turn = convo.send(DEFAULT_QUESTION)
    if not (turn.answer or "").strip():
        report.step("chat", "FAIL",
                    f"empty answer from session {turn.session_id} "
                    f"(delegations={turn.delegations})")
        return
    report.step("chat", "PASS",
                f"session {turn.session_id}: answer[{len(turn.answer)} chars], "
                f"delegations={turn.delegations}, "
                f"pending_from_coordinator={len(turn.pending_approvals)}")


def _step_grounding(report: Report, stores: dict, env: dict):
    """[3] Grounded RAG answer with verified citations (needs the DB-backed corpus)."""
    if stores["rag"] is None:
        report.step("grounding", "SKIP",
                    "no crm_app DSN configured — set UPLIFT_DB_URL (or DB_*) to verify "
                    "pgvector retrieval + citations")
        return
    from conv.rag import RagContext, answer as rag_answer
    from conv.synthesizer import AnthropicSynthesizer

    ans = rag_answer(
        DEFAULT_QUESTION,
        RagContext(
            tenant_id=env["tenant_id"],
            rag=stores["rag"],
            synthesizer=AnthropicSynthesizer(api_key=env["api_key"]),
        ),
    )
    if not ans.citations or not ans.grounded:
        report.step("grounding", "FAIL",
                    f"citations={len(ans.citations)}, grounded={ans.grounded}, "
                    f"dropped={len(ans.dropped)} — if the corpus is empty, run the ingest "
                    "sync for this tenant first (search_rag has nothing to retrieve)")
        return
    report.step("grounding", "PASS",
                f"{len(ans.citations)} citation(s), grounded={ans.grounded}, "
                f"dropped_uncited={len(ans.dropped)}")


class _SpyExecutor:
    """Wraps the real registry executor; counts calls so the gate invariant is checkable."""

    def __init__(self, inner):
        self.inner = inner
        self.calls: list = []

    def __call__(self, action):
        self.calls.append(action)
        return self.inner(action)


def _registry_executor(stores: dict):
    """The same executor seam api/asgi.make_executor wires: trusted-registry dispatch with a
    tenant-bound ToolContext (mirrored here instead of imported — importing api.asgi builds
    the whole app at module scope)."""
    from agents.tools.base import ToolContext
    from agents.tools.registry import resolve

    def execute(action):
        tool = resolve(action.name)  # KeyError on unknown tools — never default-allow
        if not action.tenant_id:
            raise ValueError("action carries no tenant binding")
        crm = stores["crm"]
        db = crm.binding() if hasattr(crm, "binding") else crm
        ctx = ToolContext(
            tenant_id=action.tenant_id, agent=action.agent, db=db,
            rag=stores["rag"], greenlight=stores["greenlight"],
        )
        return tool.invoke(ctx, **(action.payload or {}))

    return execute


def _make_action(env: dict):
    from agents.tools.registry import tool_meta
    from api.control.types import Action

    meta = tool_meta("send_email")  # server-side truth: side_effecting + channel from the class
    return Action(
        name="send_email",
        tenant_id=env["tenant_id"],
        agent="verify_agent_plane",
        side_effecting=meta["side_effecting"],
        channel=meta["channel"],
        payload={
            "to": os.environ.get(ENV_UPLIFT_VERIFY_EMAIL_TO) or DEFAULT_EMAIL_TO,
            "subject": "[uplift verify] draft only — never sent",
            "body": DRAFT_BODY,
        },
        reasoning="live agent-plane verify: prove the Greenlight pipeline end-to-end",
    )


def _step_greenlight(report: Report, stores: dict, env: dict):
    """[4] Side-effecting action -> gate -> PENDING approval; executor untouched; one trace."""
    from api.control.autonomy import AutonomyConfig
    from api.control.gate import ActionGate, GateContext
    from api.control.traces import InMemoryTraceStore

    spy = _SpyExecutor(_registry_executor(stores))
    traces = InMemoryTraceStore()
    ctx = GateContext(
        tenant_id=env["tenant_id"],
        autonomy_config=AutonomyConfig(),  # default L1: every side effect needs approval
        executor=spy,
        greenlight=stores["greenlight"],
        trace_store=traces,
    )
    result = ActionGate().run(_make_action(env), ctx)

    pending = stores["greenlight"].list_pending(env["tenant_id"])
    ours = [r for r in pending
            if (r.get("proposed_action") or {}).get("action") == "send_email"
            and r.get("agent") == "verify_agent_plane"]
    checks = {
        "status==pending_approval": result.status == "pending_approval",
        "executor NOT called": len(spy.calls) == 0,
        "approval in queue": len(ours) >= 1,
        "exactly 1 trace (pending_approval)": (
            len(traces.rows) == 1 and traces.rows[0]["kind"] == "pending_approval"
        ),
    }
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        report.step("greenlight", "FAIL", f"failed checks: {failed} (gate={result.status})")
        return None
    approval_id = ours[0]["id"]
    report.step("greenlight", "PASS",
                f"approval {approval_id} pending, executor calls=0, traces=1 (pending_approval); "
                "NO send occurred")
    return approval_id


def _step_approve(report: Report, stores: dict, env: dict, approval_id):
    """[5] Approve like a human reviewer would (the /approvals/{id}/decide path's seam)."""
    rec = stores["greenlight"].decide(
        env["tenant_id"], approval_id, "approve", decided_by="verify_agent_plane"
    )
    if rec.get("status") != "approved":
        report.step("approve", "FAIL", f"record status={rec.get('status')!r}")
        return None
    report.step("approve", "PASS", f"approval {approval_id} -> approved")
    return rec


def _step_execute(report: Report, stores: dict, env: dict, approved: dict):
    """[6] Dispatch the human-approved action through the gate+executor; assert exactly one
    'executed' trace and that the draft-only guarantee held (no real send)."""
    from api.control.autonomy import AutonomyConfig
    from api.control.gate import ActionGate, GateContext
    from api.control.traces import InMemoryTraceStore
    from api.control.types import Decision, Level

    action = _make_action(env)
    # Carry any human edits forward from the approved record (the queue is the source of truth).
    proposed = dict(approved.get("proposed_action") or {})
    proposed.pop("action", None)
    action.payload.update({k: v for k, v in proposed.items() if k in ("to", "subject", "body")})
    action.reasoning = "human-approved by verify_agent_plane (see step [5])"

    spy = _SpyExecutor(_registry_executor(stores))
    traces = InMemoryTraceStore()
    ctx = GateContext(
        tenant_id=env["tenant_id"],
        # The human grant, modeled as a tenant-scoped L3 override (see the module docstring's
        # honest-limits note + the # VERIFY on the MA tool_confirmation path).
        autonomy_config=AutonomyConfig(overrides={env["tenant_id"]: Level.L3}),
        executor=spy,
        greenlight=stores["greenlight"],
        trace_store=traces,
    )
    result = ActionGate().run(action, ctx)

    exec_result = result.result if isinstance(result.result, dict) else {}
    checks = {
        "decision==AUTO": result.decision is Decision.AUTO,
        "executor called exactly once": len(spy.calls) == 1,
        "exactly 1 trace (executed)": (
            len(traces.rows) == 1 and traces.rows[0]["kind"] == "executed"
        ),
        # Draft-only: the registry's send_email NEVER sends — its invoke yields another
        # Greenlight proposal. The 'executed' trace is the gate's; the send stays gated.
        "draft-only held (no real send)": exec_result.get("status") == "pending_approval",
    }
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        report.step("execute", "FAIL",
                    f"failed checks: {failed} (gate={result.status}, "
                    f"executor_result={exec_result.get('status')!r})")
        return
    report.step("execute", "PASS",
                "executor ran once under the human grant; exactly 1 'executed' trace; "
                "draft-only guarantee held — the dispatched send_email produced a fresh "
                "Greenlight proposal, no email left the building")


def _cleanup(stores: dict, env: dict) -> None:
    """Deny every approval this run created so the live queue is left empty (best effort)."""
    try:
        gl = stores["greenlight"]
        for rec in gl.list_pending(env["tenant_id"]):
            if rec.get("agent") == "verify_agent_plane":
                gl.decide(env["tenant_id"], rec["id"], "deny",
                          deny_message="verify_agent_plane cleanup",
                          decided_by="verify_agent_plane")
        print("[ ok ] cleanup    denied this run's leftover pending approvals")
    except Exception as exc:  # cleanup must never flip the verdict
        print(f"[warn] cleanup    best-effort cleanup failed: {exc}")


def run_live(env: dict) -> Report:
    report = Report()
    stores = _build_stores(dsn_from_env())
    if stores["crm"] is None:
        print("[note] no crm_app DSN — in-memory stores; grounding will SKIP and the workspace "
              "row cannot be loaded from Aurora (provisioning writes in-memory only)\n")

    row = _step_workspace(report, stores, env)
    if row is None:
        return report  # everything downstream rides the persisted ids

    try:
        _step_chat(report, stores, env, row)
    except Exception as exc:
        report.step("chat", "FAIL", f"{type(exc).__name__}: {exc}")
    try:
        _step_grounding(report, stores, env)
    except Exception as exc:
        report.step("grounding", "FAIL", f"{type(exc).__name__}: {exc}")

    try:
        approval_id = _step_greenlight(report, stores, env)
        approved = _step_approve(report, stores, env, approval_id) if approval_id else None
        if approved:
            _step_execute(report, stores, env, approved)
    except Exception as exc:
        report.step("greenlight-pipeline", "FAIL", f"{type(exc).__name__}: {exc}")
    finally:
        _cleanup(stores, env)
    return report


def main() -> int:
    live = _switch_env(ENV_UPLIFT_LIVE_VERIFY)
    env, missing = _required_env()
    if not live or missing:
        _print_plan(missing, live)
        return 0  # offline plan mode is always a success (CI-safe)

    print("verify_agent_plane: LIVE mode — running the end-to-end smoke\n")
    report = run_live(env)
    print("\n" + json.dumps(report.summary(), indent=2, default=str))
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
