"""Authed per-tenant workflows endpoint — the api half of the real Workflows tab
(the fourth honest-stub tab converted to REAL, after Pipeline + Contacts + Agents; the web
half is web/src/api/WorkflowsView.tsx).

One endpoint, READ-ONLY, bound to the VERIFIED JWT claims (THE TRUST RULE — tenant never from
a header or the request body):

  GET /workflows   the automation actually running the tenant's workspace: the provisioning
                   state machine. The STEP DIAGRAM is STATIC, derived from the OWNED ASL
                   semantics (signup/provisioning.py `_STEPS` + accounts.State +
                   infra/modules/provisioning/main.tf) — the funnel the machine drives is
                   signup → verify → pay → provision → activate, and that list is hardcoded
                   here with honest descriptions (the draft-gate + Greenlight story told
                   inline). The definition is NEVER fetched live: the semantics are owned
                   code, and DescribeStateMachine would return operator material (roleArn,
                   the raw ASL with Lambda ARNs) that must not reach a browser.

  recent_executions rides boto3 Step Functions `list_executions` (lazy client, machine ARN
  from shared/config PROVISIONING_SFN_ARN, max 20): execution NAME + STATUS + timestamps
  ONLY. No DescribeExecution ever — execution input/output stays unreadable by design — and
  every ARN is stripped server-side (the account id lives in the ARN; neither may leave).

KNOWN CONSTRAINT (verified live): the api task role carries states:StartExecution ONLY
(REQ-005) — no read perms. The reads will AccessDenied at runtime until REQ-009 (states:
DescribeStateMachine + ListExecutions scoped to the machine ARN) is granted. The route is
designed for that reality: AWS denial/failure degrades to HTTP 200 with
`executions_available: false` and an honest `reason` ("pending IAM grant (REQ-009)") while
the static diagram still renders — the tab stays useful, never an error wall. No ARN env
(the live posture today: api_provisioning_sfn=false) answers the same shape with reason
"not configured".

IMPORT SAFETY: importing this module touches no AWS/boto3/DB (boto3 is imported lazily
inside the request path, mirroring prod_deps.SfnProvisioningTrigger). The image-fileset
discipline extends to boto3 here: tests prove importing this route never imports it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI

from api.auth import TenantClaims

log = logging.getLogger("api.workflows")

# How many recent executions may leave the API per request. list_executions pages at 100;
# 20 is plenty for a tab and keeps the payload (and the blast radius of a noisy machine) small.
MAX_EXECUTIONS = 20

# The statuses Step Functions reports for an execution (ExecutionStatus). Anything the API
# doesn't recognize passes through verbatim — the UI renders unknown statuses neutrally.
RUNNING, SUCCEEDED, FAILED = "RUNNING", "SUCCEEDED", "FAILED"

# Honest degrade reasons (the web banner keys off `executions_available`, not these strings,
# but tests pin them so the operator story stays stable).
REASON_NOT_CONFIGURED = "not configured"
REASON_PENDING_IAM = "pending IAM grant (REQ-009)"
REASON_UNAVAILABLE = "temporarily unavailable"

# --------------------------------------------------------------------------- #
# The OWNED step diagram — the provisioning funnel exactly as the owned code
# drives it: signup/accounts.py State (created → email/phone verified → paid →
# provisioning → active), the verified-Stripe-webhook trigger (CLAUDE.md hard
# constraint #8), and the SFN machine in infra/modules/provisioning/main.tf
# whose Task states run signup/provisioning.py's idempotent steps. HARDCODED
# by design (module docstring) — a roster change here is a code change with
# tests, never a live Describe call.
# --------------------------------------------------------------------------- #
WORKFLOW_STEPS: list[dict[str, str]] = [
    {
        "id": "signup",
        "label": "Sign up",
        "description": (
            "An account is created with an email and phone. Nothing is provisioned yet — "
            "no workspace, no agents, no charge."
        ),
    },
    {
        "id": "verify",
        "label": "Verify",
        "description": (
            "Email and phone are both confirmed before payment unlocks (verify-before-pay). "
            "Verification links and codes are single-use and expire."
        ),
    },
    {
        "id": "pay",
        "label": "Pay",
        "description": (
            "Checkout completes and ONLY the cryptographically signed Stripe webhook flips "
            "the account to paid — never the browser redirect. A re-delivered webhook is a "
            "no-op: provisioning starts exactly once."
        ),
    },
    {
        "id": "provision",
        "label": "Provision",
        "description": (
            "The state machine builds the workspace step by step: tenant record, a dedicated "
            "Anthropic workspace, the eight-agent crew, identity, and defaults. Every step is "
            "idempotent (check-then-create) and a mid-failure parks the account for retry — "
            "never a half-built tenant. Outbound email stays draft-gated until sends are "
            "deliberately enabled."
        ),
    },
    {
        "id": "activate",
        "label": "Activate",
        "description": (
            "The terminal flip: the workspace goes live and the crew starts working. From "
            "here, anything an agent does that touches the outside world routes through "
            "Greenlight for human sign-off — autonomy never outruns your approval."
        ),
    },
]


# --------------------------------------------------------------------------- #
# Injected deps — the AgentsDeps/ContactsDeps pattern, with the same
# DELIBERATELY inert default: ApiDeps' default_factory builds the all-None
# stub, so a bare create_app(ApiDeps(...)) — every test, any non-asgi
# constructor — mounts the route answering the honest not-configured shape and
# NEVER builds a boto3 client as a side effect of constructing deps. The ONLY
# real wiring is api/asgi.py passing Config.provisioning_sfn_arn (REQ-005;
# un-injected on the live task today, so the deployed route answers
# not-configured until Lane Nick's deliberate flip).
# --------------------------------------------------------------------------- #
@dataclass
class WorkflowsDeps:
    # The uplift-provisioning state machine ARN (shared/config PROVISIONING_SFN_ARN).
    # None/"" = not configured -> the static diagram with executions_available: false.
    state_machine_arn: str | None = None
    # Injected fake in tests; lazily built boto3 stepfunctions client otherwise.
    sfn_client: Any = None


def _machine_name(arn: str | None) -> str:
    """The DISPLAY name of the machine — never the ARN (it carries the account id).

    A state machine ARN ends ':stateMachine:<name>'; the name is the only part that may
    leave. Unconfigured deployments fall back to the owned default name
    (infra/modules/provisioning: "${var.project}-provisioning", project=uplift)."""
    if arn:
        tail = arn.rsplit(":", 1)[-1].strip()
        if tail:
            return tail
    return "uplift-provisioning"


def _sfn(deps: WorkflowsDeps) -> Any:
    if deps.sfn_client is None:
        import boto3  # noqa: PLC0415 — lazy: importing/mounting must never need boto3

        from shared.config import load  # noqa: PLC0415 — keep import side untouched

        deps.sfn_client = boto3.client("stepfunctions", region_name=load().aws_region)
    return deps.sfn_client


def _is_access_denied(exc: Exception) -> bool:
    """Match AWS access denial across the botocore ClientError shape and fake classes:
    the AccessDeniedException code (SFN's modeled shape), a generic AccessDenied code,
    or an HTTP 403 in the response metadata."""
    if type(exc).__name__ in ("AccessDeniedException", "AccessDenied"):
        return True
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = (resp.get("Error") or {}).get("Code", "")
        if code in ("AccessDeniedException", "AccessDenied"):
            return True
        if (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode") == 403:
            return True
    return False


def _iso(value: Any) -> str | None:
    """Serialize the list_executions datetimes; tolerate fakes that already pass strings."""
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else str(value)


def _execution_entry(e: dict) -> dict:
    """ONE execution for display: name + status + timestamps ONLY (the module contract).
    The executionArn/stateMachineArn fields (account-id material) are dropped here,
    server-side — they never reach serialization."""
    return {
        "name": e.get("name"),
        "status": e.get("status"),
        "started_at": _iso(e.get("startDate")),
        "stopped_at": _iso(e.get("stopDate")),
    }


def mount_workflows(app: FastAPI, deps: WorkflowsDeps, current_tenant) -> None:
    """Mount the /workflows route on `app`, authed via `current_tenant` (the same
    verified-claims dependency every other authed route uses). Read-only: no gate deps —
    nothing here mutates, starts, stops, or describes an execution."""

    @app.get("/workflows")
    def get_workflows(claims: TenantClaims = Depends(current_tenant)):  # noqa: ARG001 — the
        # dependency IS the gate: an unauth/invalid token 401s before this body runs. The read
        # itself is tenant-independent (one shared machine; names/statuses only).
        base = {
            "machine": {
                "name": _machine_name(deps.state_machine_arn),
                "kind": "provisioning",
            },
            "steps": [dict(s) for s in WORKFLOW_STEPS],
            "step_count": len(WORKFLOW_STEPS),
        }
        if not deps.state_machine_arn:
            # The live posture today (REQ-005: PROVISIONING_SFN_ARN un-injected): the static
            # diagram still renders; nothing pretends executions exist.
            return {**base, "executions_available": False,
                    "reason": REASON_NOT_CONFIGURED, "recent_executions": []}
        try:
            resp = _sfn(deps).list_executions(
                stateMachineArn=deps.state_machine_arn, maxResults=MAX_EXECUTIONS,
            )
        except Exception as exc:  # noqa: BLE001 — narrowed immediately below
            if _is_access_denied(exc):
                # THE KNOWN CONSTRAINT (module docstring): the api task role has
                # states:StartExecution only. Honest 200 — the tab stays useful.
                reason = REASON_PENDING_IAM
            else:
                # Throttle/outage/network: degrade the same way; never leak the error text
                # (it can carry ARNs/account ids) — log the TYPE only.
                log.warning("workflows: list_executions failed (%s)", type(exc).__name__)
                reason = REASON_UNAVAILABLE
            return {**base, "executions_available": False,
                    "reason": reason, "recent_executions": []}
        executions = [_execution_entry(e) for e in (resp.get("executions") or [])]
        return {**base, "executions_available": True, "reason": None,
                "recent_executions": executions}
