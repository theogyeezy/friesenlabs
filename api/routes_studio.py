"""Agent Studio — the api half of the composer + playbook library (web/src/api/StudioView.tsx).

Authed per-tenant CRUD over `playbooks` plus the starter-template library and activation.
Every route binds the tenant from the VERIFIED JWT claims (THE TRUST RULE — tenant never from
a header or the request body; a smuggled tenant is ignored by construction since nothing reads
one). Playbooks are SPEC, NOT CODE: every definition is validated against
shared/schemas/playbook.schema.json + the owned-roster/registry cross-checks
(agents/playbooks.validate) BEFORE any write — an invalid definition is a 422, never a row.

  GET    /studio/templates                       the 5 committed starter templates (no store needed)
  GET    /studio/playbooks                       the tenant's playbooks (RLS-scoped list)
  POST   /studio/playbooks                       create (validated) -> the new row
  GET    /studio/playbooks/{id}                  one playbook (404 = absent OR another tenant's)
  PUT    /studio/playbooks/{id}                  update definition (drafts only; bumps version)
  DELETE /studio/playbooks/{id}                  delete (drafts only)
  POST   /studio/templates/{tid}/instantiate     copy a template into the tenant's library
  POST   /studio/playbooks/{id}/activate         register with the EXISTING roster mechanism
  POST   /studio/playbooks/{id}/deactivate       back to draft

ACTIVATION stays behind the EXISTING gates: agents/playbooks/activation.py registers the
playbook's owned AgentSpecs (tools narrowed, never widened) through the swappable AgentRuntime
— the registered tools come from the trusted registry, whose side-effecting members are
Policy.ALWAYS_ASK at the Tool base class, so every send/CRM write a playbook agent ever
proposes lands as a Greenlight DRAFT (draft-only invariant) and autonomy stays governed by the
per-tenant dial at execution time. Registered Managed Agents ids are TRUNCATED to a display
tail before serialization (the api/agents_routes.py contract — full ids never leave the API).
With no registrar configured, activate still flips status but reports `registered: false`
honestly (record-only) — never a fake registration.

Deps follow the inert-default contract (DealsDeps et al.): the env-built default wires the
PgPlaybookStore ONLY when the crm_app DSN is present (shared.config.dsn_from_env) and the pool
opens lazily on first use; without a DSN every store-backed route answers an honest 503.

IMPORT SAFETY: importing this module touches no DB/boto3/anthropic; psycopg2 and the
roster/registry imports are lazy (the agents_routes pattern).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from api.auth import TenantClaims

_UNCONFIGURED_DETAIL = (
    "studio not configured — no crm_app DSN on this task (DB_*/UPLIFT_DB_URL unset); "
    "playbooks are unavailable"
)
_NO_REGISTRAR_REASON = (
    "agent plane not configured on this task — the playbook is active (record-only) and will "
    "register when the agent plane is available"
)

# Display tail for registered Managed Agents ids — the api/agents_routes.py contract.
ID_TAIL_LEN = 6


def _id_tail(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value[-ID_TAIL_LEN:]


# --------------------------------------------------------------------------- #
# Deps — inert by default; env-built for prod (the integrations/public pattern,
# so api/asgi.py needs no change). Constructing deps NEVER opens a DB pool:
# PgPlaybookStore's pool is lazy (first operation).
# --------------------------------------------------------------------------- #
@dataclass
class StudioDeps:
    # PlaybookStore-shaped (agents/playbooks/store.py). None -> honest 503.
    store: Any | None = None
    # AgentRuntime-shaped registrar (create_agent/create_coordinator). None -> activation
    # flips status record-only and reports registered: false honestly. Tests inject a direct
    # runtime here; api/asgi.py wires the per-tenant `registrar_factory` below instead.
    registrar: Any | None = None
    # Per-tenant registrar resolver: (tenant_id) -> (runtime, environment_id, vault_id) | None.
    # This is the LIVE wiring (api/asgi.py): it resolves the tenant's persisted Managed Agents
    # environment from the workspace store and binds a runtime to it, so activate/run register a
    # real crew instead of being record-only. None (the default) -> falls back to `registrar`.
    registrar_factory: Any | None = None
    # PlaybookRunStore-shaped (agents/playbooks/store.py). None -> the runs route answers an
    # honest 503 and run-now history is not persisted (audit P0-2).
    run_store: Any | None = None
    # Trigger-dispatch honesty (audit P0-4): what THIS deployment actually fires. The Studio UI
    # banners schedule/event playbooks as "not yet live" when these are False — activated
    # playbooks must never read as live automation that silently isn't.
    #   scheduling_enabled — the EventBridge dispatch leg is on (owner flips
    #     playbook_dispatch_enabled in infra AND sets PLAYBOOK_DISPATCH_ENABLED=1 on the api task).
    #   events_enabled — in-process domain-event producers are wired (api/asgi.py sets this
    #     alongside the dispatcher it hands to deals/contacts routes).
    scheduling_enabled: bool = False
    events_enabled: bool = False


def build_studio_deps() -> StudioDeps:
    """Env-built default: the Pg stores ride ONLY the crm_app DSN gate every live sibling uses
    (shared.config.dsn_from_env); no DSN -> the honest all-None stub. The registrar is left
    None — live Managed Agents registration is wired deliberately, never as an import side
    effect (CLAUDE.md hard constraint #4: MA is beta, all calls behind runtime.py)."""
    import os  # noqa: PLC0415

    from shared.config import dsn_from_env  # noqa: PLC0415 — lazy, keeps import cheap

    scheduling = os.environ.get("PLAYBOOK_DISPATCH_ENABLED") == "1"
    dsn = dsn_from_env()
    if not dsn:
        return StudioDeps(scheduling_enabled=scheduling)
    from agents.playbooks.store import PgPlaybookRunStore, PgPlaybookStore  # noqa: PLC0415

    return StudioDeps(store=PgPlaybookStore(dsn), run_store=PgPlaybookRunStore(dsn),
                      scheduling_enabled=scheduling)


# --- request bodies (NONE carry tenant_id — the trust rule forbids it) ---
class PlaybookBody(BaseModel):
    definition: dict


def _require_store(deps: StudioDeps) -> Any:
    if deps.store is None:
        raise HTTPException(status_code=503, detail=_UNCONFIGURED_DETAIL)
    return deps.store


def _resolve_registrar(deps: StudioDeps, tenant_id: str):
    """Resolve (runtime, environment_id, vault_id) to register/run a playbook against for THIS
    tenant, or None for the honest record-only path. The per-tenant `registrar_factory` (live
    wiring) wins; a direct `registrar` runtime (tests) is the back-compat fallback."""
    factory = getattr(deps, "registrar_factory", None)
    if factory is not None:
        return factory(tenant_id)  # (runtime, env_id, vault_id) | None
    if deps.registrar is None:
        return None
    return (deps.registrar, getattr(deps, "environment_id", None), getattr(deps, "vault_id", None))


def _validate_or_422(definition: dict) -> None:
    from agents.playbooks import PlaybookValidationError, validate  # noqa: PLC0415 — lazy

    try:
        validate(definition)
    except PlaybookValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))


# Internal/operator columns that never reach the wire: tenant_id (the trust rule) and the
# FULL Managed Agents ids persisted for registration reuse (the agents_routes truncation
# contract — only 6-char display tails ever leave the API).
_WIRE_DROP = ("tenant_id", "ma_coordinator_id", "ma_agent_ids", "ma_registered_version")


def _has_fresh_registration(row: dict) -> bool:
    """A persisted MA registration is usable iff it matches the CURRENT definition version
    (update_definition bumps version, so an edit invalidates it by construction)."""
    return bool(row.get("ma_coordinator_id")) and \
        row.get("ma_registered_version") == row.get("version")


def _serialize(row: dict, claims: TenantClaims) -> dict:
    """One playbook row for the wire. Defense in depth: a row whose tenant_id isn't the
    verified request tenant fails loud (a silent leak must never propagate); the internal
    tenant_id + full MA ids are then dropped from the body."""
    if str(row["tenant_id"]) != str(claims.tenant_id):
        raise HTTPException(status_code=500, detail="tenant isolation violation")
    out = {k: v for k, v in row.items() if k not in _WIRE_DROP}
    out["ma_registered"] = _has_fresh_registration(row)
    for ts in ("created_at", "updated_at"):
        if out.get(ts) is not None and not isinstance(out[ts], str):
            out[ts] = out[ts].isoformat()
    return out


def _serialize_run(row: dict, claims: TenantClaims) -> dict:
    """One playbook_runs row for the wire — same loud cross-tenant guard; tenant ids dropped
    from BOTH the row and the embedded digest (the digest is the runner's as_dict, which
    carries the tenant for internal correlation)."""
    if str(row["tenant_id"]) != str(claims.tenant_id):
        raise HTTPException(status_code=500, detail="tenant isolation violation")
    out = {k: v for k, v in row.items() if k != "tenant_id"}
    if isinstance(out.get("record"), dict):
        out["record"] = {k: v for k, v in out["record"].items() if k != "tenant_id"}
    if out.get("created_at") is not None and not isinstance(out["created_at"], str):
        out["created_at"] = out["created_at"].isoformat()
    return out


def mount_studio(app: FastAPI, deps: StudioDeps, current_tenant) -> None:
    """Mount the /studio routes on `app`, authed via `current_tenant` (the same verified-claims
    dependency every other authed route uses)."""

    @app.get("/studio/templates")
    def list_studio_templates(claims: TenantClaims = Depends(current_tenant)):
        # Committed JSON, identical for every tenant — but still authed (the Studio is an
        # app surface, not a public one). No store required.
        from agents.playbooks.templates import list_templates  # noqa: PLC0415 — lazy

        return {"templates": list_templates()}

    @app.get("/studio/playbooks")
    def list_playbooks(claims: TenantClaims = Depends(current_tenant)):
        store = _require_store(deps)
        rows = store.list(claims.tenant_id)
        return {
            "playbooks": [_serialize(r, claims) for r in rows],
            # Trigger-dispatch honesty (audit P0-4): the UI banners schedule/event playbooks
            # when the corresponding leg isn't live on this deployment.
            "dispatch": {
                "scheduling_enabled": bool(getattr(deps, "scheduling_enabled", False)),
                "events_enabled": bool(getattr(deps, "events_enabled", False)),
            },
        }

    @app.post("/studio/playbooks", status_code=201)
    def create_playbook(body: PlaybookBody, claims: TenantClaims = Depends(current_tenant)):
        store = _require_store(deps)
        _validate_or_422(body.definition)
        row = store.create(claims.tenant_id, body.definition, created_by=claims.sub)
        return _serialize(row, claims)

    @app.get("/studio/playbooks/{playbook_id}")
    def get_playbook(playbook_id: str, claims: TenantClaims = Depends(current_tenant)):
        store = _require_store(deps)
        row = store.get(claims.tenant_id, playbook_id)
        if row is None:
            # Absent and another tenant's row are indistinguishable (no existence oracle).
            raise HTTPException(status_code=404, detail="no such playbook")
        return _serialize(row, claims)

    @app.put("/studio/playbooks/{playbook_id}")
    def update_playbook(playbook_id: str, body: PlaybookBody,
                        claims: TenantClaims = Depends(current_tenant)):
        store = _require_store(deps)
        row = store.get(claims.tenant_id, playbook_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such playbook")
        if row["status"] == "active":
            # An active registration must never be silently mutated: the registered roster
            # would drift from the stored definition. Deactivate first.
            raise HTTPException(status_code=409, detail="playbook is active — deactivate before editing")
        _validate_or_422(body.definition)
        updated = store.update_definition(claims.tenant_id, playbook_id, body.definition)
        if updated is None:  # deleted between the read and the write — honest 404
            raise HTTPException(status_code=404, detail="no such playbook")
        return _serialize(updated, claims)

    @app.delete("/studio/playbooks/{playbook_id}")
    def delete_playbook(playbook_id: str, claims: TenantClaims = Depends(current_tenant)):
        store = _require_store(deps)
        row = store.get(claims.tenant_id, playbook_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such playbook")
        if row["status"] == "active":
            raise HTTPException(status_code=409, detail="playbook is active — deactivate before deleting")
        if not store.delete(claims.tenant_id, playbook_id):
            raise HTTPException(status_code=404, detail="no such playbook")
        return {"deleted": True, "id": str(playbook_id)}

    @app.post("/studio/templates/{template_id}/instantiate", status_code=201)
    def instantiate_template(template_id: str, claims: TenantClaims = Depends(current_tenant)):
        store = _require_store(deps)
        from agents.playbooks.templates import get_template  # noqa: PLC0415 — lazy

        template = get_template(template_id)
        if template is None:
            raise HTTPException(status_code=404, detail="no such template")
        # Committed templates are tested-valid, but validate anyway (defense in depth — a
        # drifted template must fail loud here, never persist invalid).
        _validate_or_422(template["definition"])
        row = store.create(claims.tenant_id, template["definition"],
                           template_id=template_id, created_by=claims.sub)
        return _serialize(row, claims)

    @app.post("/studio/playbooks/{playbook_id}/activate")
    def activate_playbook_route(playbook_id: str, claims: TenantClaims = Depends(current_tenant)):
        store = _require_store(deps)
        row = store.get(claims.tenant_id, playbook_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such playbook")
        # Re-validate the STORED definition before registering (defense in depth: a row that
        # predates a schema tightening must not register unvalidated).
        _validate_or_422(row["definition"])

        registration: dict | None = None
        resolved = _resolve_registrar(deps, claims.tenant_id)
        if resolved is not None and _has_fresh_registration(row):
            # The crew for THIS definition version already exists (audit P0-3): reuse it —
            # re-activating an unchanged playbook must never mint new MA agents.
            registration = {
                "agents": [e["agent"] for e in row["definition"].get("roster", [])],
                "agent_id_tails": [_id_tail(a) for a in (row.get("ma_agent_ids") or [])],
                "coordinator_id_tail": _id_tail(row.get("ma_coordinator_id")),
                "reused": True,
            }
        elif resolved is not None:
            from agents.playbooks.activation import activate_playbook  # noqa: PLC0415 — lazy

            runtime = resolved[0]
            # Registers owned AgentSpecs through the EXISTING runtime seam (bound to THIS tenant's
            # persisted Managed Agents environment). Tools come from the trusted registry, so
            # side-effecting members stay ALWAYS_ASK (Greenlight drafts) regardless of the JSON.
            result = activate_playbook(runtime, claims.tenant_id, row["definition"])
            # Persist the FULL minted ids on the row (audit P0-3) so run/reactivate reuse the
            # crew instead of leaking a fresh one per invocation. hasattr-guarded for older
            # store fakes; a store without the seam just re-registers next time.
            if hasattr(store, "set_registration"):
                store.set_registration(
                    claims.tenant_id, playbook_id,
                    coordinator_id=result["coordinator_id"],
                    agent_ids=result["agent_ids"],
                    version=row.get("version"),
                )
            registration = {
                "agents": result["agents"],
                # TRUNCATED for display — full Managed Agents ids never leave the API.
                "agent_id_tails": [_id_tail(a) for a in result["agent_ids"]],
                "coordinator_id_tail": _id_tail(result["coordinator_id"]),
            }

        updated = store.set_status(claims.tenant_id, playbook_id, "active")
        if updated is None:
            raise HTTPException(status_code=404, detail="no such playbook")
        out = _serialize(updated, claims)
        out["registered"] = registration is not None
        if registration is not None:
            out["registration"] = registration
        else:
            out["registration_reason"] = _NO_REGISTRAR_REASON
        return out

    @app.post("/studio/playbooks/{playbook_id}/deactivate")
    def deactivate_playbook_route(playbook_id: str, claims: TenantClaims = Depends(current_tenant)):
        store = _require_store(deps)
        row = store.get(claims.tenant_id, playbook_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such playbook")
        updated = store.set_status(claims.tenant_id, playbook_id, "draft")
        if updated is None:
            raise HTTPException(status_code=404, detail="no such playbook")
        return _serialize(updated, claims)

    @app.post("/studio/playbooks/{playbook_id}/run")
    def run_playbook_route(playbook_id: str, claims: TenantClaims = Depends(current_tenant)):
        """Manual 'Run now' trigger for an active playbook.

        Tenant comes ONLY from the verified claim (THE TRUST RULE). The runner routes every
        side-effecting tool through Greenlight as a DRAFT — nothing is auto-executed. A run
        that surfaces draft actions returns status "pending"; that is correct and is NOT
        presented as "sent".
        """
        store = _require_store(deps)
        row = store.get(claims.tenant_id, playbook_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such playbook")
        if row["status"] != "active":
            raise HTTPException(status_code=409, detail="playbook is not active — activate before running")
        # Re-validate the STORED definition (defense in depth: a row that predates a schema
        # tightening must not run unvalidated).
        _validate_or_422(row["definition"])

        resolved = _resolve_registrar(deps, claims.tenant_id)
        if resolved is not None:
            from agents.playbooks.runner import TriggerEvent, run as _run_playbook  # noqa: PLC0415 — lazy

            # The factory resolved the tenant's runtime + its persisted MA environment/vault, so
            # the run drives the real crew. (For tests' direct registrar these are None, which
            # create_session accepts.)
            runtime, environment_id, vault_id = resolved
            event = TriggerEvent(kind="manual", name="run-now")
            record = _run_playbook(
                runtime, store, claims.tenant_id, playbook_id, event,
                environment_id=environment_id, vault_id=vault_id,
                run_store=deps.run_store,  # persist the digest as tenant history (audit P0-2)
            )
            # Serialize the RunRecord. Use as_dict() if present (the public API); draft actions
            # surface as status "pending" — correct, NOT "sent". Never fabricate a run result.
            run_dict = record.as_dict() if hasattr(record, "as_dict") else {
                k: v for k, v in vars(record).items() if not k.startswith("_")
            }
            # The tenant_id in the record is the verified claim tenant (set by the runner from
            # the upstream claim). Drop it from the wire body — internal ids never leave the API.
            run_dict.pop("tenant_id", None)
            return {"ran": True, "run": run_dict}
        else:
            # No agent plane configured: flip nothing, report honestly. Mirror the activate
            # record-only shape (_NO_REGISTRAR_REASON / registered:false pattern).
            out = _serialize(row, claims)
            out["ran"] = False
            out["run_reason"] = _NO_REGISTRAR_REASON
            return out

    @app.get("/studio/playbooks/{playbook_id}/runs")
    def list_playbook_runs(playbook_id: str, limit: int = 50,
                           claims: TenantClaims = Depends(current_tenant)):
        """Run history for one playbook (audit P0-2) — newest first, bounded. The rows are the
        runner-persisted RunRecord digests: status, trigger, surfaced draft proposals. Tenant
        from the verified claim only; absent and another tenant's playbook are the same 404."""
        if not 1 <= limit <= 200:
            raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
        store = _require_store(deps)
        row = store.get(claims.tenant_id, playbook_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such playbook")
        if deps.run_store is None:
            raise HTTPException(status_code=503, detail=(
                "run history not configured on this task — runs execute but their digests "
                "are not persisted here"))
        rows = deps.run_store.list(claims.tenant_id, playbook_id, limit=limit)
        return {"runs": [_serialize_run(r, claims) for r in rows]}
