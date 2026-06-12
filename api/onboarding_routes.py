"""Authed per-tenant ONBOARDING / first-run endpoints — the api half of the first-run experience
(the web half is web/src/onboarding/*).

Three endpoints, ALL bound to the VERIFIED JWT claim (THE TRUST RULE — the tenant is NEVER read
from a header or the request body; it comes only from `custom:tenant_id`):

  GET  /onboarding               the calling tenant's first-run state: which checklist steps are
                                 done, whether the tour was dismissed, whether sample data landed.
                                 No row yet => the honest fresh default (nothing done) — never an
                                 invented "complete" state.
  PUT  /onboarding               upsert the tenant's first-run state (a step toggles done, the tour
                                 is dismissed). Idempotent: re-PUTting the same body converges.
  POST /onboarding/load-sample   ONE-CLICK "Load sample data": load the committed demo fixture into
                                 THE CALLING TENANT via the EXISTING loader (scripts/demo/
                                 load_demo_tenant.py — reused, not reinvented), through the same
                                 RLS-bound crm_app SET LOCAL pattern, idempotently (wipe-then-insert
                                 — re-running NEVER duplicates). On success the `sample_loaded` flag
                                 + the "load_data" checklist step are marked done so the populated
                                 views surface immediately, and the loaded row counts are reported.
                                 ALSO seeds the SAMPLE KNOWLEDGE PAGES (the audit's "onboarding
                                 never touches knowledge" gap): three short, clearly-labelled
                                 markdown pages through the SAME ingest seam the Knowledge tab's
                                 add/edit path uses (chunk → embed → upsert + the `#raw` original,
                                 so they land EDITABLE in the pages rail). Idempotent (same
                                 title+content → same ref namespace, in-place upsert). HONEST
                                 DEGRADE: no ingest plane wired -> `knowledge.pages_seeded: 0` +
                                 a reason; a seeding failure NEVER fails the CRM load that
                                 already happened — it is reported, not hidden.

All three ride the same crm_app DSN every live surface (/contacts, /deals, /views) rides — RLS via
the per-op `SET LOCAL app.current_tenant` transaction (the _PgTenantClient pattern). The onboarding
row is upserted (INSERT .. ON CONFLICT (tenant_id) DO UPDATE), never deleted (crm_app has no DELETE
on onboarding_state — db/roles.sql / REQ-010). Unconfigured (no crm_app DSN -> no store injected)
GET/PUT degrade to an honest in-memory-default / 503-on-write rather than inventing persistence.

WRITE-PATH SAFETY: /load-sample is a tenant-scoped DATA-SEED of a fabricated, zero-PII fixture into
the caller's OWN tenant — it sends NO email/SMS and makes NO external CRM write, so it does not pass
through the Greenlight side-effect gate (which guards OUTBOUND tools). It is guarded instead by: the
verified-claim tenant binding, RLS (every INSERT scoped to the caller's tenant by SET LOCAL), and
idempotency (wipe-then-insert). It is a no-op-safe, owner-initiated, in-tenant action.

IMPORT SAFETY: importing this module touches no AWS/boto3/DB — psycopg2 and the loader are imported
lazily, only inside the connect/load paths in real use (the production API image's fileset
regression test imports api.app, which mounts this module).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from api.auth import TenantClaims

log = logging.getLogger("api.onboarding")

# The first-run checklist's step ids (the SINGLE source of truth the web checklist mirrors). A
# flat allow-list: a PUT may only toggle these — an unknown step id is a 422, never persisted, so
# the `steps` jsonb can never accrete arbitrary keys from a hostile body.
STEP_IDS: tuple[str, ...] = ("load_data", "try_chat", "invite_team")

# The sample knowledge pages load-sample seeds (clearly labelled, written to be EDITED —
# they land as editable pages in the Knowledge rail via the same ingest seam as the tab's
# own add path). Markdown stays inside the safe subset the web renderer supports.
SAMPLE_PAGES: tuple[tuple[str, str], ...] = (
    ("Pricing and discounts (sample)",
     "## Standard rates\n\nEvery service is quoted from the current price book. Prices are "
     "reviewed quarterly.\n\n## Discounts\n\n- Standard discounts cap at **15%** without "
     "owner approval\n- Seasonal promotions are pre-approved and published in advance\n"
     "- Stacking promotions is not permitted\n\nThis is a sample page — edit it with your "
     "real pricing, and your agents will quote from it."),
    ("Refunds and returns (sample)",
     "## Returns\n\nItems can be returned within **30 days** with proof of purchase for a "
     "full refund. Opened consumables are non-returnable unless defective.\n\n## Refund "
     "handling\n\nRefunds go back to the original payment method within 5 business days. "
     "Refund requests route to a human for approval before completion.\n\nThis is a sample "
     "page — replace it with your real policy."),
    ("Customer FAQ (sample)",
     "## Hours\n\nMonday to Friday, 9:00am to 5:30pm.\n\n## Common questions\n\n- *Where is "
     "my order?* Check the confirmation email for tracking, or ask us with the order "
     "number.\n- *Can I change an appointment?* Yes — up to 24 hours before, at no "
     "charge.\n\nThis is a sample page — add the questions your customers actually ask."),
)

# The honest reason strings the knowledge-seed half of load-sample reports (pinned by tests).
REASON_PAGES_UNCONFIGURED = (
    "sample pages not seeded — the ingest plane (INGEST_REAL_STORES + a DSN) is not wired"
)
REASON_PAGES_FAILED = "sample page seeding failed — the CRM sample still loaded"

_UNCONFIGURED_DETAIL = (
    "onboarding data plane not configured — no crm_app DSN on this task "
    "(DB_*/UPLIFT_DB_URL unset); first-run state cannot be persisted"
)


# --------------------------------------------------------------------------- #
# Wire bodies — NONE carry tenant_id (the trust rule forbids it).
# --------------------------------------------------------------------------- #
class OnboardingPutBody(BaseModel):
    """PUT /onboarding body: a partial update. Only the provided fields change.

    `steps` is a flat map of step-id -> bool over the STEP_IDS allow-list (an unknown id is a 422).
    `dismissed` flips the first-run tour off. There is NO `sample_loaded` here: that flag is set
    ONLY by a real /onboarding/load-sample success, never by a client claim.
    """

    steps: dict[str, bool] | None = None
    dismissed: bool | None = Field(default=None)


# --------------------------------------------------------------------------- #
# Persistence — the onboarding_state row, RLS-scoped via the per-op SET LOCAL
# transaction (the same _PgTenantClient pattern every tenant store rides).
# --------------------------------------------------------------------------- #
def _default_state(tenant_id: str) -> dict:
    """The honest fresh-tenant default: nothing done, not dismissed, no sample data.

    Returned when no row exists yet (a brand-new tenant) OR when the data plane is unconfigured
    (GET degrades to this rather than 503 — first-run UI should still render its checklist)."""
    return {
        "tenant_id": str(tenant_id),
        "steps": {sid: False for sid in STEP_IDS},
        "dismissed": False,
        "sample_loaded": False,
    }


def _normalize_steps(raw: Any) -> dict[str, bool]:
    """Coerce a persisted/`raw` steps map to the full allow-listed shape (missing -> False, extra
    keys dropped). Keeps the wire shape stable as STEP_IDS evolves."""
    raw = raw if isinstance(raw, dict) else {}
    return {sid: bool(raw.get(sid, False)) for sid in STEP_IDS}


class OnboardingStateStore:
    """Aurora-backed per-tenant onboarding state (FORCE'd RLS on onboarding_state).

    Reuses the shared connection plumbing (`_PgTenantClient`: pool + per-op `SET LOCAL
    app.current_tenant` transaction) so RLS scopes every read/write and the GUC auto-resets at
    COMMIT. Writes are UPSERTs (`ON CONFLICT (tenant_id) DO UPDATE`) — the row is durable per-tenant
    state, never deleted by the app (crm_app has no DELETE — db/roles.sql).
    """

    def __init__(self, dsn: str | None = None, *,
                 conn_factory: Callable[[], Any] | None = None):
        # Lazy import keeps this module import-safe (no psycopg2 needed to import api.app).
        from api.pg_clients import _PgTenantClient  # noqa: PLC0415

        self._pg = _PgTenantClient(dsn, conn_factory=conn_factory)

    def get(self, tenant_id) -> dict:
        """The tenant's onboarding row, normalized to the wire shape. No row yet -> the fresh
        default (a brand-new tenant has done nothing). RLS-scoped."""
        from api.pg_clients import _dict_one  # noqa: PLC0415

        with self._pg._tx(tenant_id) as cur:
            cur.execute(
                "SELECT tenant_id, steps, dismissed, sample_loaded "
                "FROM onboarding_state WHERE tenant_id = %s",
                (str(tenant_id),),
            )
            row = _dict_one(cur)
        if row is None:
            return _default_state(tenant_id)
        return {
            "tenant_id": str(row.get("tenant_id")),
            "steps": _normalize_steps(row.get("steps")),
            "dismissed": bool(row.get("dismissed")),
            "sample_loaded": bool(row.get("sample_loaded")),
        }

    def upsert(self, tenant_id, *, steps: dict[str, bool] | None = None,
               dismissed: bool | None = None, sample_loaded: bool | None = None) -> dict:
        """Merge a partial update onto the tenant's row and return the merged state.

        Read-merge-write inside ONE tenant-scoped transaction (RLS WITH CHECK enforces tenant_id ==
        app.current_tenant on both the INSERT and UPDATE arm). Only provided fields change; `steps`
        is merged key-by-key (a PUT toggling one step never clears the others)."""
        from psycopg2.extras import Json  # noqa: PLC0415 — lazy
        from api.pg_clients import _dict_one  # noqa: PLC0415

        with self._pg._tx(tenant_id) as cur:
            cur.execute(
                "SELECT steps, dismissed, sample_loaded FROM onboarding_state "
                "WHERE tenant_id = %s FOR UPDATE",
                (str(tenant_id),),
            )
            cur_row = _dict_one(cur)
            merged_steps = _normalize_steps(cur_row.get("steps") if cur_row else {})
            if steps:
                for sid, done in steps.items():
                    if sid in merged_steps:
                        merged_steps[sid] = bool(done)
            new_dismissed = (bool(dismissed) if dismissed is not None
                             else bool(cur_row.get("dismissed")) if cur_row else False)
            new_sample = (bool(sample_loaded) if sample_loaded is not None
                          else bool(cur_row.get("sample_loaded")) if cur_row else False)
            cur.execute(
                "INSERT INTO onboarding_state (tenant_id, steps, dismissed, sample_loaded) "
                "VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "steps = EXCLUDED.steps, dismissed = EXCLUDED.dismissed, "
                "sample_loaded = EXCLUDED.sample_loaded, updated_at = now()",
                (str(tenant_id), Json(merged_steps), new_dismissed, new_sample),
            )
        return {
            "tenant_id": str(tenant_id),
            "steps": merged_steps,
            "dismissed": new_dismissed,
            "sample_loaded": new_sample,
        }

    def _getconn(self):
        """A raw pooled connection (the demo loader does its own SET LOCAL + commit on it)."""
        return self._pg._getconn()

    def _putconn(self, conn) -> None:
        self._pg._putconn(conn)


# --------------------------------------------------------------------------- #
# Sample-data loader — reuse the EXISTING demo loader, tenant-scoped.
# --------------------------------------------------------------------------- #
def _load_sample_into_tenant(store: OnboardingStateStore, tenant_id: str,
                             *, fixture_path: str | None = None) -> dict:
    """Load the demo fixture into `tenant_id` via scripts/demo/load_demo_tenant.py.

    Reuses that loader's `load(conn, dataset, tenant_id=, embedder=)` verbatim — the SAME idempotent
    wipe-then-insert under `SET LOCAL app.current_tenant` the live demo path uses. A raw pooled
    connection is handed in (the loader commits/rolls back on it); we always return it to the pool.
    Returns the per-table row counts the loader reports. The embedder is the offline deterministic
    stub by default, Titan V2 only under INGEST_REAL_STORES=1 (the loader's build_embedder seam).

    `fixture_path` defaults to the committed demo fixture (the production path); tests override it
    with a throwaway fixture so a shared test DB never collides on the demo's fixed global PKs."""
    import importlib.util  # noqa: PLC0415

    here = os.path.dirname(os.path.abspath(__file__))
    loader_path = os.path.join(here, "..", "scripts", "demo", "load_demo_tenant.py")
    spec = importlib.util.spec_from_file_location("load_demo_tenant", loader_path)
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)  # type: ignore[union-attr]

    dataset = loader.read_fixture(fixture_path) if fixture_path else loader.read_fixture()
    embedder = loader.build_embedder()
    conn = store._getconn()
    try:
        conn.autocommit = False
        counts = loader.load(conn, dataset, tenant_id=tenant_id, embedder=embedder)
    finally:
        store._putconn(conn)
    return counts


# --------------------------------------------------------------------------- #
# Injected deps — the ContactsDeps/DealsDeps pattern: the all-None default mounts
# routes answering honest 503s and NEVER opens a DB pool as a side effect of
# constructing deps. api/asgi.py passes the real store wired from the crm_app DSN.
# --------------------------------------------------------------------------- #
@dataclass
class OnboardingDeps:
    # The onboarding-state store (None = data plane unconfigured). GET degrades to the honest
    # in-memory default; PUT + load-sample answer the honest 503 (no fake persistence).
    store: OnboardingStateStore | None = None
    # Override the sample loader in tests (so load-sample can be exercised without a real loader
    # run). Default: the real reuse of scripts/demo/load_demo_tenant.py.
    sample_loader: Callable[[OnboardingStateStore, str], dict] = _load_sample_into_tenant
    # The SAME (tenant_id, title, content) ingest seam the Knowledge tab's add/edit path rides
    # (api/knowledge_routes.build_doc_ingestor) — load-sample seeds SAMPLE_PAGES through it so
    # they land as EDITABLE pages. None = ingest plane unwired -> pages_seeded: 0 + the honest
    # reason, never a fake success.
    ingest_document: Callable[[str, str, str], Any] | None = None


def deps_from_dsn(dsn: str | None,
                  ingest_document: Callable[[str, str, str], Any] | None = None) -> OnboardingDeps:
    """Build OnboardingDeps from the crm_app DSN (None -> the honest-unconfigured stub). Called by
    api/asgi.py with `dsn_from_env()` — the SAME DSN every live surface rides — plus the document
    ingestor the knowledge routes already build (one seam, not a parallel pipeline)."""
    if not dsn:
        return OnboardingDeps(ingest_document=ingest_document)
    return OnboardingDeps(store=OnboardingStateStore(dsn), ingest_document=ingest_document)


def _require_store(deps: OnboardingDeps) -> OnboardingStateStore:
    if deps.store is None:
        raise HTTPException(status_code=503, detail=_UNCONFIGURED_DETAIL)
    return deps.store


def mount_onboarding(app: FastAPI, deps: OnboardingDeps, current_tenant) -> None:
    """Mount /onboarding routes on `app`, authed via `current_tenant` (the same verified-claims
    dependency every other authed route uses). The tenant is ALWAYS the verified claim."""

    @app.get("/onboarding")
    def get_onboarding(claims: TenantClaims = Depends(current_tenant)):
        # Unconfigured data plane: still render the honest fresh default so the first-run UI works
        # in any deploy — never a 503 that would blank the whole shell on a brand-new tenant.
        if deps.store is None:
            return _default_state(claims.tenant_id)
        return deps.store.get(claims.tenant_id)

    @app.put("/onboarding")
    def put_onboarding(body: OnboardingPutBody, claims: TenantClaims = Depends(current_tenant)):
        store = _require_store(deps)
        if body.steps is not None:
            unknown = sorted(set(body.steps) - set(STEP_IDS))
            if unknown:
                raise HTTPException(
                    status_code=422,
                    detail=f"unknown onboarding step(s): {', '.join(unknown)}",
                )
        return store.upsert(
            claims.tenant_id, steps=body.steps, dismissed=body.dismissed,
        )

    @app.post("/onboarding/load-sample")
    def load_sample(claims: TenantClaims = Depends(current_tenant)):
        # Tenant-scoped, idempotent demo-fixture seed into the CALLER's OWN tenant (no outbound
        # side effect — see the module docstring's WRITE-PATH SAFETY note). Reuses the existing
        # loader through the same SET LOCAL RLS path; re-running NEVER duplicates.
        store = _require_store(deps)
        counts = deps.sample_loader(store, claims.tenant_id)

        # Sample knowledge pages, through the SAME seam as Knowledge → add page (idempotent:
        # unchanged title+content upserts in place). The CRM fixture above already landed —
        # a pages failure is REPORTED, never converted into a failed load.
        knowledge: dict = {"pages_seeded": 0, "reason": None}
        if deps.ingest_document is None:
            knowledge["reason"] = REASON_PAGES_UNCONFIGURED
        else:
            try:
                for title, content in SAMPLE_PAGES:
                    deps.ingest_document(claims.tenant_id, title, content)
                    knowledge["pages_seeded"] += 1
            except Exception as exc:  # noqa: BLE001 — loud in the server log (type only on the
                # wire-adjacent message; the raw error can carry AWS detail), honest on the wire.
                log.error("onboarding: sample page seeding failed after %d page(s) (%s)",
                          knowledge["pages_seeded"], type(exc).__name__)
                knowledge["reason"] = REASON_PAGES_FAILED

        # Mark the load done so the populated views surface immediately. A persistence failure here
        # must NOT pretend the data didn't load — record it honestly but still report the counts.
        state: dict | None = None
        try:
            state = store.upsert(
                claims.tenant_id, sample_loaded=True, steps={"load_data": True},
            )
        except Exception:  # noqa: BLE001 — the data DID load; surface that truth regardless
            state = None
        return {"loaded": True, "counts": counts, "knowledge": knowledge, "onboarding": state}
