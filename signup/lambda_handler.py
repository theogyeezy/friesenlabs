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

SECRET RESOLUTION (PR #181 contract — infra/modules/provisioning_lambda): the Lambda env
carries Secrets Manager ARN REFERENCES only (`*_SECRET_ARN`), never resolved secret values —
plan-time resolution would land every value in Terraform state, and a rotation would need a
re-apply to reach the function. `_resolve_secret_env()` runs once at cold start, BEFORE
`build_provisioner()`, fetching each present ARN via boto3 (the same `GetSecretValue` shape
`api/migrate.py:_secret` uses) and exporting the VALUE env names `shared.config.Config` /
`dsn_from_env` read. Absent ARN vars are skipped (the all-stub posture needs nothing);
resolution failures RAISE loudly — visible in the SFN execution history — never silently stub.

Import-safe: importing this module touches no env, no boto3, no DB, no api/ packages — the
prod_deps import and the boto3 client both happen lazily inside the first invocation.
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

# Each *_SECRET_ARN env the provisioning-Lambda module may inject (PR #181) -> the VALUE env
# name(s) shared/config.py reads. CRM_APP_SECRET_ARN is the one JSON-shaped secret (the
# RDS-managed {"username","password"} doc — the same env name + boto3 fetch `api/migrate.py`
# already resolves); every other secret is a plain string.
_SECRET_ENV_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("CRM_APP_SECRET_ARN", ("DB_USER", "DB_PASS")),                # JSON {username, password}
    ("RESEND_API_KEY_SECRET_ARN", ("RESEND_API_KEY",)),
    ("ANTHROPIC_ADMIN_KEY_SECRET_ARN", ("ANTHROPIC_ADMIN_KEY",)),
    ("POSTHOG_PROJECT_KEY_SECRET_ARN", ("POSTHOG_PROJECT_KEY_VALUE",)),
    ("ANTHROPIC_API_KEY_SECRET_ARN", ("ANTHROPIC_API_KEY",)),
    ("UPLIFT_ENV_ID_SECRET_ARN", ("UPLIFT_ENV_ID",)),
)

# Cold-start cache: built on the first invocation, reused for the container's lifetime.
_PROVISIONER = None


def _resolve_secret_env(sm=None) -> list[str]:
    """Resolve each present `*_SECRET_ARN` env into the VALUE env names Config reads.

    Returns the list of value env names that were set (for logging/tests — never the values).
    Each ARN var is OPTIONAL: absent -> skipped (stub posture, deploy invariance holds); when
    none are present no boto3 client is even built. A present ARN that fails to resolve
    (missing secret, denied, malformed JSON) raises out of the handler — Step Functions
    surfaces it in the execution history, which beats a silent fall-through to stubs. The
    ARN-resolved value is the source of truth on this function: it overwrites any pre-set
    value env of the same name (the infra module never injects both shapes).
    """
    present = [(arn_env, targets) for arn_env, targets in _SECRET_ENV_MAP
               if os.environ.get(arn_env)]
    if not present:
        return []
    if sm is None:  # pragma: no cover — tests inject a fake client; prod builds boto3 here
        import boto3  # noqa: PLC0415 — lazy (import-safety; only when an ARN is present)
        sm = boto3.client("secretsmanager",
                          region_name=os.environ.get("AWS_REGION", "us-east-1"))

    resolved: list[str] = []
    for arn_env, targets in present:
        secret = sm.get_secret_value(SecretId=os.environ[arn_env])["SecretString"]
        if arn_env == "CRM_APP_SECRET_ARN":
            creds = json.loads(secret)  # raise loudly on junk — never half-configure the DSN
            os.environ["DB_USER"] = str(creds["username"])
            os.environ["DB_PASS"] = str(creds["password"])
        else:
            (target,) = targets
            os.environ[target] = secret
        resolved.extend(targets)
    log.info("resolved %d secret ARN(s) into env: %s", len(present), ", ".join(resolved))
    return resolved


def _get_provisioner():
    global _PROVISIONER  # noqa: PLW0603 — the Lambda cold-start singleton
    if _PROVISIONER is None:
        # ARN -> value env BEFORE the build: build_provisioner reads Config()/dsn_from_env at
        # construction time. A resolution failure leaves _PROVISIONER unset, so the next
        # invocation retries instead of caching a half-configured runtime.
        _resolve_secret_env()
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
