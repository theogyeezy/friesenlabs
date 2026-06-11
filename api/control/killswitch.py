"""Kill switch (Build Guide Phase 5, Step 33).

A per-tenant (and global) pause flag the gate checks BEFORE any execute. Flipping it blocks new
actions; live, it also interrupts running sessions (user.interrupt) — that call is authored + flagged
"verify" and not made here.
"""
from __future__ import annotations


class KillSwitch:
    def __init__(self):
        self._global = False
        self._tenants: set[str] = set()

    def pause_global(self) -> None:
        self._global = True

    def resume_global(self) -> None:
        self._global = False

    def pause_tenant(self, tenant_id: str) -> None:
        self._tenants.add(tenant_id)

    def resume_tenant(self, tenant_id: str) -> None:
        self._tenants.discard(tenant_id)

    def is_paused(self, tenant_id: str) -> bool:
        return self._global or tenant_id in self._tenants

    # --- the /control/killswitch route surface (api/routes_control.py) ---
    # The SAME status/set shape as api/control/settings.py PersistedKillSwitch, so the routes
    # serve either interchangeably: in-memory here (offline/unconfigured — instance-local by
    # design), Pg-backed there (prod — shared across API tasks). Authorization (who may flip
    # which scope) is the ROUTE's job; these just flip state.
    def status(self, tenant_id: str) -> dict:
        if self._global:
            return {"engaged": True, "scope": "global"}
        if tenant_id in self._tenants:
            return {"engaged": True, "scope": "tenant"}
        return {"engaged": False, "scope": "tenant"}

    def set(self, tenant_id: str, engaged: bool, *, scope: str = "tenant") -> None:
        if scope == "global":
            self._global = bool(engaged)
        elif scope == "tenant":
            if engaged:
                self._tenants.add(tenant_id)
            else:
                self._tenants.discard(tenant_id)
        else:
            raise ValueError(f"scope must be 'tenant' or 'global', got {scope!r}")

    def interrupt_event(self, session_id: str) -> dict:
        """The MA interrupt event for a paused session (VERIFY; not sent here)."""
        return {"type": "user.interrupt", "session_id": session_id}
