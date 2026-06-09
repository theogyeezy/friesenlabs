"""Per-tenant provisioning pipeline (Build Guide Phase 10, Step 55).

Runs ONLY on the verified payment webhook. Each tenant gets its own dedicated Anthropic workspace so
credentials + limits are hard-isolated. The pipeline MUST be:
  - idempotent: every step is check-then-create, so a re-delivered webhook never double-provisions;
  - rollback-safe: a mid-failure parks the account in provisioning_failed (for retry) and rolls back
    partial resources — you never want a half-built tenant or a charged customer with no instance.

All external systems are injected (db, anthropic_admin, secrets, cognito, cube, resend); live calls
are BLOCKED: needs Nick. The Anthropic Admin API workspace/key endpoints are flagged "verify".
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .accounts import Account, State


@dataclass
class ProvisionResult:
    ok: bool
    tenant_id: str | None = None
    failed_step: str | None = None
    steps_done: list[str] = field(default_factory=list)


class Provisioner:
    def __init__(self, *, store, mint_tenant_id, db, anthropic_admin, secrets, cognito, cube, resend,
                 agent_plane=None):
        self.store = store
        self.mint_tenant_id = mint_tenant_id      # injected (deterministic in tests)
        self.db = db
        self.admin = anthropic_admin
        self.secrets = secrets
        self.cognito = cognito
        self.cube = cube
        self.resend = resend
        self.agent_plane = agent_plane            # builds env+agents+coordinator in the workspace

    def provision(self, account: Account) -> ProvisionResult:
        # Guard: provisioning is only valid after payment, and is idempotent if already done.
        if account.state is State.ACTIVE:
            return ProvisionResult(True, account.tenant_id, steps_done=["already_active"])
        if account.state not in (State.PAID, State.PROVISIONING, State.PROVISIONING_FAILED):
            raise ValueError(f"cannot provision from state {account.state.value} (must be PAID)")

        account.state = State.PROVISIONING
        self.store.update(account)
        done: list[str] = []
        created: dict = {}

        try:
            # 1. Tenant record + tenant_id (check-then-create on the account's existing tenant_id).
            tenant_id = account.tenant_id or self.mint_tenant_id(account.id)
            account.tenant_id = tenant_id
            self.db.upsert_tenant(tenant_id=tenant_id, account_id=account.id)  # idempotent upsert
            created["tenant"] = tenant_id
            done.append("tenant_record")

            # 2. Anthropic workspace + scoped key -> Secrets Manager (key never returned again).
            ws_id = self.admin.ensure_workspace(tenant_id)        # check-then-create (verify)
            created["workspace"] = ws_id
            if not self.secrets.exists(f"uplift/{tenant_id}/anthropic_key"):
                key = self.admin.create_workspace_key(ws_id, tenant_id)
                self.secrets.put(f"uplift/{tenant_id}/anthropic_key", key)
            self.admin.set_limits(ws_id, tenant_id)               # per-workspace spend + rate limits
            done.append("workspace")

            # 3. Agent plane in that workspace (env + specialists + coordinator).
            if self.agent_plane is not None:
                self.agent_plane.ensure(tenant_id=tenant_id, workspace_id=ws_id)
            done.append("agent_plane")

            # 4. Set Cognito custom:tenant_id (now that it exists) + confirm the account.
            self.cognito.set_tenant_id(account.cognito_sub, tenant_id)
            self.cognito.confirm(account.cognito_sub)
            done.append("cognito_tenant")

            # 5. Cube tenant context + budget/cost tags + autonomy defaults.
            self.cube.ensure_tenant_context(tenant_id)
            self.db.set_tenant_defaults(tenant_id)                # autonomy defaults, cost tags
            done.append("tenant_context")

            # 6. Welcome email + flip active.
            self.resend.send_welcome(account.email, tenant_id)
            account.state = State.ACTIVE
            self.store.update(account)
            done.append("welcome")

            return ProvisionResult(True, tenant_id, steps_done=done)

        except Exception as e:  # noqa: BLE001 — park + roll back partial resources
            self._rollback(account, created, done)
            account.state = State.PROVISIONING_FAILED
            account.meta["provisioning_error"] = f"{type(e).__name__}: {e}"
            self.store.update(account)
            return ProvisionResult(False, account.tenant_id, failed_step=_next_step(done),
                                   steps_done=done)

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
