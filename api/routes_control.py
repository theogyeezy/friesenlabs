"""Authed per-tenant control surface — the accountability pitch made real:

  GET/PUT /control/killswitch   {engaged: bool, scope: "tenant"|"global"}
  GET/PUT /control/autonomy     {level: int 0..3}
  GET     /control/traces       ?limit=&cursor=  ->  {traces: [...], cursor}

CONTRACT (the web lane builds against exactly these shapes — do not change):
  killswitch -> {"engaged": bool, "scope": str}            ("global" wins when engaged)
  autonomy   -> {"level": int}                              (0..3 == L0..L3)
  traces     -> {"traces": [{id, ts, tool, decision, status, summary}], "cursor": str|null}

Every route is bound to the VERIFIED JWT claims (THE TRUST RULE — tenant never from a header,
query, or the request body). Backing state is the persisted control plane wired in api/asgi.py
(PersistedKillSwitch / PersistedAutonomyDial / PgTraceStore over Aurora); the in-memory deps
defaults serve offline/tests with identical shapes.

AUTHORIZATION DECISION (v2 — RBAC over the verified `cognito:groups` claim; supersedes the v1
"every authed tenant user is an admin" posture that the security audit flagged):
  * WRITES (PUT killswitch tenant scope, PUT autonomy) are TENANT-ADMIN ONLY: gated by
    api.auth.is_tenant_admin over the verified `cognito:groups` claim (the ONE admin policy —
    "admin" group membership, with the documented empty-groups back-compat allowance until
    RBAC_STRICT=1 retires it). READS stay open to every authed tenant user — a paused tenant
    deserves to see WHY its agents stopped.
  * GLOBAL-scope kill switch: OPERATOR-USER-ONLY (user-granular — the v1 tenant-granular
    allowlist meant EVERY user of an operator tenant could pause the whole platform). The
    caller's VERIFIED `sub` or `email` must appear in the CONTROL_GLOBAL_OPERATOR_USERS env
    allowlist (comma-separated Cognito subs and/or emails; emails compared case-insensitively,
    subs byte-for-byte; set on the API task by Lane Nick). Unset/empty = NOBODY may flip global
    (fail closed); everyone else gets 403. Identity still comes only from the verified claim —
    the env var is read at request time so a rotation needs no restart. The route-level admin
    gate applies too: a global operator flips through the same admin-gated PUT. (New env name
    to be folded into shared/config.py by its owning lane — recorded in the PR description;
    the legacy CONTROL_GLOBAL_OPERATOR_TENANTS no longer grants anything.)
"""
from __future__ import annotations

import logging
import os

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from api.auth import TenantClaims, make_current_admin
from api.control.settings import AutonomyDial
from api.control.traces import DEFAULT_TRACE_LIMIT, MAX_TRACE_LIMIT, _minimize
from api.control.types import Level

log = logging.getLogger("api.control")

# Comma-separated Cognito user identifiers (subs and/or emails) allowed to flip the GLOBAL
# kill-switch scope. USER-granular by design (security audit): the previous
# CONTROL_GLOBAL_OPERATOR_TENANTS allowlist let every user of an operator tenant pause the
# entire platform. The python constant keeps its historical name so existing imports hold.
ENV_CONTROL_GLOBAL_OPERATORS = "CONTROL_GLOBAL_OPERATOR_USERS"

# int wire level <-> Level enum (index == wire value).
_LEVELS = (Level.L0, Level.L1, Level.L2, Level.L3)

# trace `kind` -> the wire `decision` (the gate's Decision the run resolved to).
_DECISION_BY_KIND = {"executed": "auto", "pending_approval": "approve", "blocked": "block"}


class KillSwitchBody(BaseModel):
    engaged: bool
    scope: str = "tenant"   # "tenant" (default) | "global" (operator-only)


class AutonomyBody(BaseModel):
    level: int


def _global_operator_entries() -> set[str]:
    """The env-allowlisted operator USERS (read per request — rotation needs no restart).

    Entries are Cognito subs and/or emails, comma-separated; strip + drop empties.
    Unset/empty = empty set = nobody may flip global (fail closed).
    """
    raw = os.environ.get(ENV_CONTROL_GLOBAL_OPERATORS, "")
    return {t.strip() for t in raw.split(",") if t.strip()}


def _is_global_operator(claims: TenantClaims) -> bool:
    """USER-granular operator check for the global kill-switch scope.

    Allowed iff the caller's VERIFIED `sub` (byte-for-byte) or `email` (case-insensitive —
    email local/domain case is not identity-significant) appears in the allowlist. Both come
    only from the verified JWT claims, never a header or body.
    """
    entries = _global_operator_entries()
    if not entries:
        return False  # unset/empty = NOBODY (fail closed)
    if claims.sub and claims.sub in entries:
        return True
    email = (claims.email or "").strip().lower()
    return bool(email) and email in {e.lower() for e in entries}


def _trace_wire(row: dict) -> dict:
    """One trace row in the EXACT web-lane wire shape: {id, ts, tool, decision, status, summary}."""
    kind = row.get("kind")
    summary = row.get("reasoning") or ""
    return {
        "id": str(row.get("id")),
        "ts": row.get("ts"),
        "tool": row.get("tool"),
        "decision": _DECISION_BY_KIND.get(kind, kind),
        "status": kind,
        "summary": _minimize(summary) if summary else "",
    }


def mount_control(app: FastAPI, deps, current_tenant) -> None:
    """Mount the /control routes on `app`, authed via `current_tenant` (the same verified-claims
    dependency every other authed route uses). `deps` is the ApiDeps bag (duck-typed to avoid an
    api.app import cycle): killswitch + trace_store are the SAME objects the gate consults, and
    the dial is the SAME persisted level the gate's autonomy_config resolves — flip it here,
    the very next gate run obeys it.

    Writes are tenant-admin-gated (`current_admin` — the api.auth admin policy over the
    verified `cognito:groups` claim); reads stay on `current_tenant` for every tenant user."""
    current_admin = make_current_admin(current_tenant)

    def _dial():
        # The Pg-backed dial when api/asgi.py wired one; else the in-memory dial over the gate's
        # own AutonomyConfig.overrides (instance-local, but gate-visible immediately).
        return deps.autonomy_dial or AutonomyDial(deps.autonomy_config)

    @app.get("/control/killswitch")
    def get_killswitch(claims: TenantClaims = Depends(current_tenant)):
        return deps.killswitch.status(claims.tenant_id)

    @app.put("/control/killswitch")
    def put_killswitch(body: KillSwitchBody, claims: TenantClaims = Depends(current_admin)):
        # ADMIN-GATED write (current_admin): a non-admin tenant member can read the switch but
        # never flip it. The global scope layers the user-granular operator check on top.
        if body.scope not in ("tenant", "global"):
            raise HTTPException(status_code=422,
                                detail="scope must be 'tenant' or 'global'")
        if body.scope == "global" and not _is_global_operator(claims):
            # Operator-USER-only (see the module-docstring authorization decision). 403, never
            # 404 — the scope exists; this caller may not flip it.
            raise HTTPException(status_code=403,
                                detail="global kill switch is operator-only")
        deps.killswitch.set(claims.tenant_id, body.engaged, scope=body.scope)
        log.info("killswitch %s scope=%s tenant=%s by=%s",
                 "ENGAGED" if body.engaged else "released", body.scope,
                 claims.tenant_id, claims.sub)
        return deps.killswitch.status(claims.tenant_id)

    @app.get("/control/autonomy")
    def get_autonomy(claims: TenantClaims = Depends(current_tenant)):
        return {"level": _LEVELS.index(_dial().get(claims.tenant_id))}

    @app.put("/control/autonomy")
    def put_autonomy(body: AutonomyBody, claims: TenantClaims = Depends(current_admin)):
        """Set the tenant's persisted autonomy level — the dial the gate reads on every run.

        ADMIN-GATED write (current_admin): raising autonomy widens what auto-executes, so only
        a tenant admin may turn the dial; any tenant user may still read it.

        Level semantics (api/control/autonomy.py `decide`; read-only actions ALWAYS auto-run,
        validated + traced — the level governs side-effecting actions only):
          0 (L0) — suggest only: EVERYTHING side-effecting needs human approval; nothing executes.
          1 (L1) — ask first: every side effect routes to Greenlight for approval (the default).
          2 (L2) — act within limits: auto-executes only under the value/discount thresholds;
                   anything over (or with no declared value at stake) needs approval.
          3 (L3) — reads auto-run and routine writes may auto-execute, but flagged cases still
                   pause for approval — and EVERY side-effecting tool remains draft-only behind
                   Greenlight (the Phase 4 guarantee), so nothing real sends without a human.
        """
        if not 0 <= body.level <= 3:
            raise HTTPException(status_code=422, detail="level must be an integer 0..3")
        _dial().set(claims.tenant_id, _LEVELS[body.level])
        log.info("autonomy level=%s tenant=%s by=%s", body.level, claims.tenant_id, claims.sub)
        return {"level": body.level}

    @app.get("/control/traces")
    def get_traces(limit: int = DEFAULT_TRACE_LIMIT, cursor: str | None = None,
                   claims: TenantClaims = Depends(current_tenant)):
        n = max(1, min(int(limit), MAX_TRACE_LIMIT))
        try:
            rows, next_cursor = deps.trace_store.list(
                tenant_id=claims.tenant_id, limit=n, cursor=cursor)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid cursor")
        # Defense in depth (the repo-wide re-check): never return a row whose tenant_id isn't
        # the verified request tenant — a silent RLS leak fails loud, not propagates.
        for r in rows:
            if str(r.get("tenant_id")) != str(claims.tenant_id):
                raise HTTPException(status_code=500, detail="tenant isolation violation")
        return {"traces": [_trace_wire(r) for r in rows], "cursor": next_cursor}
