"""Orphan-roster reaper — delete the Managed-Agents agents of SUPERSEDED rosters (2026-06-14).

Managed-Agents agents are created once per tenant and frozen in; the self-upgrading roster
(agents/provisioning.py) mints a FRESH coordinator + specialists whenever the code's specs change and
repoints the tenant at them. The old roster is then dead weight in the shared MA environment, and it
accumulates — every deploy that changes a spec, times every active tenant, plus any provision that
lost the cross-process claim — until it pushes against the per-environment agent ceiling.

`agents/provisioning.upgrade_roster` records each superseded roster in the `retired_rosters` ledger
(an RLS-EXEMPT ops table, so this CROSS-tenant sweep can read it — no role on Aurora can bypass a
FORCE'd tenant policy). This module reaps those rows: after a grace window it deletes the recorded
agents from MA and marks the ledger row reaped.

SAFE BY CONSTRUCTION — three independent guards, any one of which suffices:
  1. It only ever targets coordinators the system EXPLICITLY recorded as superseded (never a scan
     that could misclassify a live coordinator — which would be unsafe given no cross-tenant read of
     the current set is even possible).
  2. Every provision mints fresh specialists (new ids), so a retired roster's specialists are unique
     to it and can never be pinned by a current coordinator.
  3. The grace window keeps a just-retired roster (whose old coordinator may still be draining an
     in-flight turn, or whose upgrade is still racing) untouched until it has settled.

Default DRY-RUN: the reaper reports what it WOULD delete and changes nothing unless apply=True.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Protocol

log = logging.getLogger("agents.retirement")

# Default grace: a retired roster is left alone this long before reaping (an old coordinator can keep
# draining an in-flight turn for a while, and a racing upgrade must fully settle first).
DEFAULT_GRACE_SECONDS = 3600


def due_retirements(rows: list[dict], now: datetime, grace_seconds: int) -> list[dict]:
    """The reapable subset of ledger rows: not yet reaped AND retired longer ago than the grace
    window. Pure — the single, tested home of the grace rule (the Pg source filters reaped_at in SQL
    via the partial index, then this enforces the age uniformly)."""
    out: list[dict] = []
    for r in rows:
        if r.get("reaped_at") is not None:
            continue
        retired_at = r.get("retired_at")
        if retired_at is None or (now - retired_at).total_seconds() >= grace_seconds:
            out.append(r)
    return out


class RetirementSource(Protocol):
    """What the reaper needs from the retired_rosters ledger (cross-tenant, RLS-exempt)."""

    def list_unreaped(self) -> list[dict]: ...
    def mark_reaped(self, row_id: Any) -> None: ...


class InMemoryRetirementSource:
    """Offline ledger source (tests/dev). `rows` are plain dicts with id/tenant_id/coordinator_id/
    agent_ids/retired_at/reaped_at — the same shape PgRetirementSource yields."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.reaped: list[Any] = []

    def list_unreaped(self) -> list[dict]:
        return [r for r in self.rows if r.get("reaped_at") is None]

    def mark_reaped(self, row_id: Any) -> None:
        self.reaped.append(row_id)
        for r in self.rows:
            if r.get("id") == row_id:
                r["reaped_at"] = "reaped"


def reap_orphans(runtime: Any, source: RetirementSource, *, now: datetime,
                 grace_seconds: int = DEFAULT_GRACE_SECONDS, apply: bool = False) -> dict:
    """Delete the agents of every due retired roster from MA and mark the ledger row reaped.

    For each due row the delete TARGETS are the stored specialist ids UNION the coordinator's
    currently-pinned specialists resolved from MA (covers the WIN-case retirement, which stores an
    empty agent_ids — the row only knew the coordinator id) UNION the coordinator itself. Deletes are
    best-effort and idempotent (a missing id is a no-op); a row is marked reaped ONLY if every delete
    in its roster succeeded, so a partial failure leaves the row due and a later run retries the
    stragglers. apply=False (default) reports the targets and changes nothing.

    Returns a report: {apply, considered, due, rosters:[{row_id, tenant_id, coordinator_id, targets,
    deleted, failed, reaped, deferred}]}. `deferred` is a WIN-case row whose specialists couldn't be
    resolved (MA listing down) — left due so a later sweep reaps it whole rather than orphaning them.
    """
    rows = source.list_unreaped()
    due = due_retirements(rows, now, grace_seconds)

    # Resolve the live MA topology ONCE so a WIN-case retirement (empty agent_ids) can find the
    # superseded coordinator's pinned specialists. Best-effort: if MA can't be listed we still reap
    # whatever ids the ledger stored, and DEFER any row whose specialists we therefore couldn't see.
    pinned_by_coord: dict[str, list[str]] = {}
    listed_ok = True
    if due:
        try:
            for a in runtime.list_agents():
                if a.get("is_coordinator") and a.get("id"):
                    pinned_by_coord[a["id"]] = [x for x in (a.get("agents") or []) if x]
        except Exception:  # noqa: BLE001 — degrade to stored ids only
            listed_ok = False
            log.warning("reaper: could not list MA agents; reaping stored ids only", exc_info=True)

    report: dict = {"apply": apply, "considered": len(rows), "due": len(due), "rosters": []}
    for r in due:
        coord = r.get("coordinator_id")
        stored = [x for x in r.get("agent_ids", []) if x]
        # Specialists = stored ids UNION the coordinator's currently-pinned specialists (resolved
        # from MA, covering the WIN-case empty agent_ids). De-dup, stable order.
        specialists = list(dict.fromkeys(stored + pinned_by_coord.get(coord, [])))
        entry = {"row_id": r.get("id"), "tenant_id": r.get("tenant_id"), "coordinator_id": coord,
                 "targets": list(dict.fromkeys(specialists + ([coord] if coord else []))),
                 "deleted": [], "failed": [], "reaped": False, "deferred": False}

        if not apply:
            report["rosters"].append(entry)
            continue

        # DEFER a WIN-case row (no stored specialist ids) whose specialists we couldn't resolve
        # because the MA listing failed: reaping just the coordinator would orphan them forever.
        # Leave it due so a later sweep with a working listing reaps the whole roster.
        if not stored and not specialists and not listed_ok:
            entry["deferred"] = True
            report["rosters"].append(entry)
            continue

        ok_specialists = True
        for aid in specialists:
            try:
                runtime.delete_agent(aid)
                entry["deleted"].append(aid)
            except Exception:  # noqa: BLE001 — one bad delete must not abort the sweep
                entry["failed"].append(aid)
                ok_specialists = False
                log.warning("reaper: failed to delete agent %s (ledger row %s)",
                            aid, r.get("id"), exc_info=True)
        # Delete the coordinator (the WIN-case resolution anchor) ONLY once every specialist is gone;
        # a partial failure keeps it so the next sweep can re-resolve the survivors from MA.
        if coord and ok_specialists:
            try:
                runtime.delete_agent(coord)
                entry["deleted"].append(coord)
            except Exception:  # noqa: BLE001
                entry["failed"].append(coord)
                log.warning("reaper: failed to delete coordinator %s (ledger row %s)",
                            coord, r.get("id"), exc_info=True)
        if not entry["failed"]:
            source.mark_reaped(r["id"])
            entry["reaped"] = True
        report["rosters"].append(entry)

    log.info("reaper: considered=%d due=%d apply=%s reaped=%d",
             report["considered"], report["due"], apply,
             sum(1 for e in report["rosters"] if e["reaped"]))
    return report


class PgRetirementSource:
    """Aurora-backed ledger source over the RLS-EXEMPT `retired_rosters` table. Reads EVERY tenant's
    unreaped rows in one pass (the reaper is a cross-tenant ops sweep) and marks rows reaped.
    Import-safe: psycopg2 is imported lazily on construction."""

    def __init__(self, dsn: str):
        import psycopg2  # noqa: PLC0415 — guarded
        from psycopg2.extras import RealDictCursor  # noqa: PLC0415
        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = True
        self._cursor_factory = RealDictCursor

    def list_unreaped(self) -> list[dict]:
        # reaped_at IS NULL uses the partial index; the grace window is applied by due_retirements.
        with self._conn.cursor(cursor_factory=self._cursor_factory) as cur:
            cur.execute(
                "SELECT id, tenant_id, coordinator_id, agent_ids, retired_at, reaped_at "
                "FROM retired_rosters WHERE reaped_at IS NULL ORDER BY retired_at"
            )
            return [dict(r) for r in cur.fetchall()]

    def mark_reaped(self, row_id: Any) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE retired_rosters SET reaped_at = now() "
                "WHERE id = %s AND reaped_at IS NULL",
                (row_id,),
            )

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass
