"""Provisioning Lambda — the Step Functions Task entrypoint (TODO INT/P1; infra REQ-005).

THE CONTRACT (infra/modules/provisioning/main.tf): every Task state invokes this handler with
``{"account_id": ..., "step": <s>}`` where ``<s>`` is one of the six build steps
(tenant_record | workspace | agent_plane | cognito_tenant | tenant_context | welcome), or the
terminal ``activate`` / ``park_failed`` flips. One IDEMPOTENT Provisioner step runs per
invocation (check-then-create / plain overwrite — `signup.provisioning.Provisioner.run_step`),
so the machine's Retry policy (3 attempts, backoff) re-runs safely and a duplicate execution
against an already-ACTIVE account degrades to structured skips.

FAILURE SHAPE: build-step errors RAISE out of the handler — Step Functions owns retries, and
the Catch-all routes to ``park_failed`` (a state-only flip + the at-most-once refund seam; it
never raises). Successful invocations return structured dicts ({account_id, step, status,
state, tenant_id, ...}) for SFN Choice states / execution-history forensics.

RETRY ENTRYPOINT (TODO INT/P2, operator-invoked — NOT an SFN state): invoking the Lambda
directly with ``{"account_id": ..., "step": "retry"}`` re-provisions a parked
(provisioning_failed) account in-process via the idempotent full pipeline. It is itself
idempotent: an ACTIVE account is a skip, any other non-parked state is a structured refusal.
The logic lives in `signup.provisioning.Provisioner.retry` — ONE implementation shared with
the gated POST /signup/{account_id}/retry-provision route (api/signup_routes.py) so the two
retry surfaces can never drift.

COLD START builds the clients from env exactly once, via `api.prod_deps.build_provisioner` —
the SAME selection path the API task uses, so the SIGNUP_REAL_DEPS master switch is honored:
without it the runtime is all-stub and touches nothing real, no matter what other env vars are
present (deploy invariance). The draft-gate (ALLOW_REAL_SENDS) rides along on the senders.

Import-safe: importing this module touches no env, no boto3, no DB, no api/ packages — the
prod_deps import happens lazily inside the first invocation.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Cold-start cache: built on the first invocation, reused for the container's lifetime.
_PROVISIONER = None


def _get_provisioner():
    global _PROVISIONER  # noqa: PLW0603 — the Lambda cold-start singleton
    if _PROVISIONER is None:
        from api.prod_deps import build_provisioner  # noqa: PLC0415 — lazy (import-safety)
        _PROVISIONER = build_provisioner()
    return _PROVISIONER


def handler(event, context=None):
    """The Lambda entrypoint (REQ-005 wires it as ``signup.lambda_handler.handler``)."""
    event = event or {}
    account_id = str(event.get("account_id") or "")
    step = str(event.get("step") or "")
    if not account_id or not step:
        raise ValueError("provisioning event must carry account_id and step")

    prov = _get_provisioner()
    account = prov.store.get(account_id)
    if account is None:
        # Fail LOUDLY (visible in the execution history) — the trigger only ever starts an
        # execution for an account that exists in the shared store, so a miss means a foreign /
        # stub-mode invocation, never something to silently absorb. (park_failed for a phantom
        # account raises too: there is nothing to park.)
        raise ValueError(f"no such account: {account_id}")

    if step == "retry":
        # Idempotent operator retry — Provisioner.retry (shared with the gated
        # /signup/{account_id}/retry-provision route; module docstring).
        result = prov.retry(account)
    else:
        result = prov.run_step(account, step)
    log.info("provisioning step %s for account %s -> %s", step, account_id,
             result.get("status"))
    return {"account_id": account_id, **result}
