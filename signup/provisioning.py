"""Per-tenant provisioning pipeline (Build Guide Phase 10, Step 55).

Runs ONLY on the verified payment webhook. Each tenant gets its own dedicated Anthropic workspace so
credentials + limits are hard-isolated. The pipeline MUST be:
  - idempotent: every step is check-then-create, so a re-delivered webhook never double-provisions;
  - rollback-safe: a mid-failure parks the account in provisioning_failed (for retry) and rolls back
    partial resources — you never want a half-built tenant or a charged customer with no instance.

All external systems are injected (db, anthropic_admin, secrets, cognito, cube, resend); live calls
are BLOCKED: needs Nick. The Anthropic Admin API workspace/key endpoints are flagged "verify".

TWO EXECUTION SHAPES over the SAME step functions (so they can never drift):
  - `provision(account)` — the in-process path (the on_paid default): all steps in one call, with
    the partial-resource rollback on mid-failure.
  - `run_step(account, step)` — ONE idempotent step per call: the Step Functions Task contract
    (`infra/modules/provisioning/main.tf` invokes the Lambda in `signup/lambda_handler.py` with
    `{account_id, step}`). Build-step failures RAISE so the SFN Retry policy re-runs them;
    `activate` / `park_failed` are the machine's terminal state-only flips. Because each
    invocation reloads the Account from the shared store, `_step_tenant_record` persists the
    minted tenant_id immediately and the later steps re-resolve the workspace id idempotently
    (check-then-create) instead of carrying it in memory.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .accounts import Account, State

log = logging.getLogger(__name__)


@dataclass
class ProvisionResult:
    ok: bool
    tenant_id: str | None = None
    failed_step: str | None = None
    steps_done: list[str] = field(default_factory=list)


def refund_stub(account: Account) -> str:
    """Default terminal-failure refund seam: RECORDS the need, moves NO money.

    # VERIFY (Stripe refund endpoint, TODO INT/P2): the real callback is POST /v1/refunds with the
    # payment_intent (or the subscription invoice's charge) from the completed checkout session —
    # an id the Account does NOT currently persist (only stripe_customer_id). Confirm the exact
    # endpoint/params + decide refund-vs-retry policy (an auto-refund followed by a successful
    # retry would leave a refunded-AND-active tenant) before injecting a live callback. Until
    # then this stub logs + the park records `refund_requested` for operational follow-up.
    """
    log.warning(
        "REFUND NEEDED (stub — no money moved): account %s, stripe_customer_id=%s; "
        "terminal provisioning failure parked the account",
        account.id, account.stripe_customer_id,
    )
    return "stub_recorded"


class Provisioner:
    def __init__(self, *, store, mint_tenant_id, db, anthropic_admin, secrets, cognito, cube, resend,
                 agent_plane=None, funnel=None, workspace_store=None, refund=None,
                 tenant_defaults=None):
        self.store = store
        self.mint_tenant_id = mint_tenant_id      # injected (deterministic in tests)
        self.db = db
        self.admin = anthropic_admin
        self.secrets = secrets
        self.cognito = cognito
        self.cube = cube
        self.resend = resend
        self.agent_plane = agent_plane            # builds env+agents+coordinator in the workspace
        self.funnel = funnel                      # optional signup.funnel.Funnel; None = no-op
        # Optional agents.workspace_store.WorkspaceStore: persists the per-tenant Managed Agents
        # ids so the conversation factory + worker read them back instead of rebuilding the roster
        # per request. None = skip (offline tests / DB not configured).
        self.workspace_store = workspace_store
        # Optional terminal-failure refund callback(account) -> str (INT/P2). None = the
        # record-only `refund_stub` (# VERIFY there) — park_failed fires it AT MOST ONCE per
        # account (the `refund_requested` meta flag) and NEVER lets it raise out of the park.
        self.refund = refund
        # Optional signup.tenant_defaults.PgTenantDefaults (INT/P2 "tenant-context
        # correctness"): the REAL step-5 seeder of the tenant_settings row (default autonomy
        # level + cost tag, SET LOCAL pattern). None = step 5 falls back to `db` — the _Noop in
        # an unconfigured deploy, a recorder in tests.
        self.tenant_defaults = tenant_defaults

    # ------------------------------------------------------------------ full pipeline
    def provision(self, account: Account) -> ProvisionResult:
        # Guard: provisioning is only valid after payment, and is idempotent if already done.
        if account.state is State.ACTIVE:
            return ProvisionResult(True, account.tenant_id, steps_done=["already_active"])
        if account.state not in (State.PAID, State.PROVISIONING, State.PROVISIONING_FAILED):
            raise ValueError(f"cannot provision from state {account.state.value} (must be PAID)")
        # L2: defense-in-depth — never provision an unverified account even if it somehow reached
        # PAID. VERIFY BEFORE PAY is enforced upstream; this is the belt-and-suspenders check.
        if not account.fully_verified:
            raise ValueError("cannot provision: account is not fully verified (email + phone)")

        account.state = State.PROVISIONING
        self.store.update(account)
        done: list[str] = []
        created: dict = {}

        try:
            for step in _STEPS:
                getattr(self, f"_step_{step}")(account, created)
                done.append(step)
            self.activate(account)
            return ProvisionResult(True, account.tenant_id, steps_done=done)

        except Exception as e:  # noqa: BLE001 — park + roll back partial resources
            self._rollback(account, created, done)
            self.park_failed(account, error=f"{type(e).__name__}: {e}")
            return ProvisionResult(False, account.tenant_id, failed_step=_next_step(done),
                                   steps_done=done)

    # ------------------------------------------------------------------ single-step API (SFN)
    def run_step(self, account: Account, step: str) -> dict:
        """Run exactly ONE provisioning step — the Step Functions Task contract.

        Build steps share `provision()`'s guards, are idempotent (check-then-create / plain
        overwrite), and RAISE on failure (the machine's Retry/Catch owns the policy — no
        in-process rollback here; a retried step reuses the half-created resource instead of
        orphaning it). `activate` / `park_failed` are the terminal state-only flips. Returns are
        structured (`step` / `status` / `state` / `tenant_id`) for SFN Choice states.
        """
        if step == "park_failed":
            return self.park_failed(account)
        if step == "activate":
            return self.activate(account)
        if step not in _STEPS:
            raise ValueError(f"unknown provisioning step {step!r} (steps: {_STEPS})")
        if account.state is State.ACTIVE:
            # A re-delivered/duplicate execution against a finished account is a no-op step.
            return self._result(step, account, status="skipped", reason="already_active")
        if account.state not in (State.PAID, State.PROVISIONING, State.PROVISIONING_FAILED):
            raise ValueError(f"cannot provision from state {account.state.value} (must be PAID)")
        if not account.fully_verified:
            raise ValueError("cannot provision: account is not fully verified (email + phone)")
        if account.state is not State.PROVISIONING:
            account.state = State.PROVISIONING
            self.store.update(account)
        getattr(self, f"_step_{step}")(account, None)
        return self._result(step, account, status="ok")

    def activate(self, account: Account) -> dict:
        """Terminal SUCCESS flip (state-only): PROVISIONING -> ACTIVE + the server-side funnel.

        Idempotent: an already-ACTIVE account is a skip. Refuses any state other than
        PROVISIONING — `activate` must never short-circuit payment/verification/the build steps.
        """
        if account.state is State.ACTIVE:
            return self._result("activate", account, status="skipped", reason="already_active")
        if account.state is not State.PROVISIONING:
            raise ValueError(
                f"cannot activate from state {account.state.value} (must be provisioning)"
            )
        account.state = State.ACTIVE
        self.store.update(account)
        # H7: server-side funnel — record the instance as provisioned and group the user
        # under their tenant. Optional/injected — None is a no-op (offline tests).
        if self.funnel is not None:
            self.funnel.capture(account.id, "instance_provisioned", tenant_id=account.tenant_id)
            self.funnel.group_tenant(account.id, account.tenant_id)
        return self._result("activate", account, status="ok")

    def park_failed(self, account: Account, error: str | None = None) -> dict:
        """Terminal FAILURE flip (state-only) + the at-most-once refund seam.

        NEVER raises: this is the SFN Catch-all's final state (no Retry/Catch of its own) and
        the in-process except path — a park that crashed would strand the account mid-state.
        The SFN machine passes no error detail (Parameters are {account_id, step} only), so a
        missing `error` records a generic marker rather than clobbering an earlier, more
        specific one.
        """
        account.state = State.PROVISIONING_FAILED
        if error:
            account.meta["provisioning_error"] = error
        elif "provisioning_error" not in account.meta:
            account.meta["provisioning_error"] = "unknown (parked by the SFN catch-all)"
        refund_status = self._request_refund(account)
        try:
            self.store.update(account)
        except Exception:  # noqa: BLE001 — never raise out of the park (docstring)
            log.exception("park_failed: store update failed for account %s", account.id)
        # H7: the terminal failure is a server-side funnel event too (provisioning_failed,
        # grouped under the tenant when one was minted — a pre-tenant_record park has none).
        # GUARDED: park_failed must never raise, and an analytics hiccup must never affect the
        # park or the refund seam (the prod PostHogClient never raises, but the funnel is an
        # injected duck — defend anyway).
        if self.funnel is not None:
            try:
                self.funnel.capture(account.id, "provisioning_failed",
                                    tenant_id=account.tenant_id,
                                    error=account.meta.get("provisioning_error"))
            except Exception:  # noqa: BLE001 — never raise out of the park (docstring)
                log.exception("park_failed: funnel capture failed for account %s", account.id)
        return self._result("park_failed", account, status="ok", refund=refund_status)

    def retry(self, account: Account) -> dict:
        """Idempotent operator/tenant retry: provisioning_failed -> re-provision (TODO INT/P2).

        ONE implementation shared by both retry surfaces so they can never drift:
          * the operator Lambda entrypoint (`signup/lambda_handler.py`, direct invoke with
            ``{"account_id", "step": "retry"}`` — IAM-gated by lambda:InvokeFunction);
          * the gated POST /signup/{account_id}/retry-provision route
            (`api/signup_routes.py` — SIGNUP_REAL_DEPS + verified-claims tenant match).

        Itself idempotent: an ACTIVE account is a skip; any other non-parked state is a
        structured refusal (never a stealth re-provision); only a parked
        (provisioning_failed) account re-runs the idempotent full pipeline.
        """
        if account.state is State.ACTIVE:
            return {"step": "retry", "status": "skipped", "reason": "already_active",
                    "state": account.state.value, "tenant_id": account.tenant_id}
        if account.state is not State.PROVISIONING_FAILED:
            return {"step": "retry", "status": "refused",
                    "reason": f"state is {account.state.value}, not provisioning_failed",
                    "state": account.state.value, "tenant_id": account.tenant_id}
        res = self.provision(account)   # the idempotent full pipeline (check-then-create steps)
        return {"step": "retry", "status": "ok" if res.ok else "failed",
                "state": account.state.value, "tenant_id": res.tenant_id,
                "failed_step": res.failed_step, "steps_done": res.steps_done}

    def _request_refund(self, account: Account) -> str:
        """Fire the injected refund callback AT MOST ONCE per account; never raise."""
        if account.meta.get("refund_requested"):
            return "already_requested"
        account.meta["refund_requested"] = True
        try:
            return (self.refund or refund_stub)(account) or "requested"
        except Exception as e:  # noqa: BLE001 — park_failed must complete regardless
            account.meta["refund_error"] = f"{type(e).__name__}: {e}"
            log.exception("refund callback failed for account %s", account.id)
            return "error"

    @staticmethod
    def _result(step: str, account: Account, *, status: str, **extra) -> dict:
        return {"step": step, "status": status, "state": account.state.value,
                "tenant_id": account.tenant_id, **extra}

    # ------------------------------------------------------------------ the idempotent steps
    def _step_tenant_record(self, account: Account, created: dict | None) -> None:
        # 1. Tenant record + tenant_id (check-then-create on the account's existing tenant_id).
        tenant_id = account.tenant_id or self.mint_tenant_id(account.id)
        account.tenant_id = tenant_id
        self.db.upsert_tenant(tenant_id=tenant_id, account_id=account.id)  # idempotent upsert
        # Persist immediately: the SFN path reloads the Account per invocation, so the minted
        # tenant_id must survive this step (the in-process path persists it a little earlier
        # than before, which is harmless).
        self.store.update(account)
        if created is not None:
            created["tenant"] = tenant_id

    def _step_workspace(self, account: Account, created: dict | None) -> None:
        # 2. Anthropic workspace + scoped key -> Secrets Manager (key never returned again).
        ws_id = self._workspace_id(account)
        if created is not None:
            created["workspace"] = ws_id
        if not self.secrets.exists(f"uplift/{account.tenant_id}/anthropic_key"):
            key = self.admin.create_workspace_key(ws_id, account.tenant_id)
            self.secrets.put(f"uplift/{account.tenant_id}/anthropic_key", key)
        self.admin.set_limits(ws_id, account.tenant_id)   # per-workspace spend + rate limits

    def _step_agent_plane(self, account: Account, created: dict | None) -> None:
        # 3. Agent plane in that workspace (env + specialists + coordinator) — then PERSIST
        # the per-tenant ids (tenant_workspaces row) so the request path never re-provisions.
        if self.agent_plane is None:
            return
        ws_id = self._workspace_id(account)   # idempotent re-resolve (check-then-create)
        plane = self.agent_plane.ensure(tenant_id=account.tenant_id, workspace_id=ws_id) or {}
        if self.workspace_store is not None:
            self.workspace_store.upsert(
                account.tenant_id,
                plane.get("workspace_id", ws_id),
                plane.get("environment_id"),
                plane.get("coordinator_id"),
            )

    def _step_cognito_tenant(self, account: Account, created: dict | None) -> None:
        # 4. Set Cognito custom:tenant_id (now that it exists) + confirm the account.
        self._require_tenant(account)
        self.cognito.set_tenant_id(account.cognito_sub, account.tenant_id)
        self.cognito.confirm(account.cognito_sub)

    def _step_tenant_context(self, account: Account, created: dict | None) -> None:
        # 5. Tenant-context defaults — two halves with DIFFERENT realities:
        #    (a) `cube.ensure_tenant_context` is EXPLICITLY a no-op in production (the prod_deps
        #        _Noop is the PERMANENT wiring, not a pending TODO): Cube has no per-tenant
        #        resource to provision — its security context is derived per REQUEST from the
        #        verified JWT (semantic/security.js queryRewrite + the tenant-scoped Cube JWT
        #        minted in agents/tools/cube_client.py). The call stays so injected fakes can
        #        observe/veto the step in tests.
        #    (b) `set_tenant_defaults` is REAL: seed the tenant_settings row (default autonomy
        #        level + cost tag) via the injected `tenant_defaults`
        #        (signup/tenant_defaults.PgTenantDefaults under SIGNUP_REAL_DEPS + DSN —
        #        idempotent ON CONFLICT DO NOTHING, SET LOCAL pattern); the `db` fallback keeps
        #        the historic seam (_Noop unconfigured / recorder in tests).
        self._require_tenant(account)
        self.cube.ensure_tenant_context(account.tenant_id)   # documented no-op (see above)
        (self.tenant_defaults or self.db).set_tenant_defaults(account.tenant_id)

    def _step_welcome(self, account: Account, created: dict | None) -> None:
        # 6. Welcome email (the activate flip is its own terminal step).
        self.resend.send_welcome(account.email, account.tenant_id)

    def _workspace_id(self, account: Account):
        self._require_tenant(account)
        return self.admin.ensure_workspace(account.tenant_id)

    @staticmethod
    def _require_tenant(account: Account) -> None:
        """SFN defense: a step that needs the tenant_id must fail loudly (-> Retry/park) if
        tenant_record somehow has not persisted one, never run against tenant_id=None."""
        if not account.tenant_id:
            raise ValueError("tenant_record has not run: account has no tenant_id")

    def _rollback(self, account: Account, created: dict, done: list[str]) -> None:
        """Best-effort teardown of partial resources so retry starts clean (no orphaned workspace)."""
        ws = created.get("workspace")
        if ws is not None and hasattr(self.admin, "delete_workspace"):
            try:
                self.admin.delete_workspace(ws)
            except Exception:  # noqa: BLE001
                pass  # leave for a sweeper; never raise out of rollback


_STEPS = ["tenant_record", "workspace", "agent_plane", "cognito_tenant", "tenant_context", "welcome"]


def _next_step(done: list[str]) -> str:
    for s in _STEPS:
        if s not in done:
            return s
    return "done"
