"""Playbook trigger DISPATCH — the firing mechanism the runner was missing.

``agents/playbooks/runner.py`` *executes* an activated playbook when a trigger fires, but nothing
fired them: schedule/event triggers were schema-supported and runner-supported yet inert. This is
the dispatcher that closes that gap.

Two trigger surfaces, both over the SAME tenant-scoped ``PlaybookStore`` + ``runner.run`` seam:

  * SCHEDULE — an EventBridge rule (infra/modules/playbooks) RunTasks
    ``python -m agents.playbooks.dispatch --schedule --all`` on a fixed cadence; for each tenant it
    runs every ACTIVE playbook whose cron is due at the current minute.
  * EVENT — ``PlaybookDispatcher.dispatch_event(tenant_id, event_name, payload)`` is the in-process
    seam a domain event (e.g. ``deal.created``) calls to run every active ``event`` playbook bound
    to that name. The first producer is wired: a successful ``POST /deals`` create fires
    ``deal.created`` through ``api/deals_routes.py`` (guarded + inert without a dispatcher); more
    domain-event sites can adopt the same seam.

THE TRUST RULE: the tenant is the scheduler's/event-source's TRUSTED arg, never read from an event
body. Every run is contained — one bad playbook never crashes the dispatch (runner.run already
returns a ``status="error"`` record), and side effects stay draft-only through Greenlight.

IMPORT SAFETY: importing this module touches no AWS/DB/Anthropic; real clients are built only inside
``main()`` in real mode, mirroring ingest/run_sync.py.
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable

from agents.playbooks import STATUS_ACTIVE
from agents.playbooks.runner import RunRecord, TriggerEvent

log = logging.getLogger("agents.playbooks.dispatch")


# --------------------------------------------------------------------------- #
# Minimal 5-field cron matcher (no external dependency — the repo avoids deps).
# Fields: minute hour day-of-month month day-of-week. Supports  *  a  a-b  a,b
# and step  */n / a-b/n . day-of-week 0 and 7 both mean Sunday.
# --------------------------------------------------------------------------- #
def _expand_field(field: str, lo: int, hi: int) -> set[int]:
    """Expand one cron field to the set of integers it matches in [lo, hi]."""
    out: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError(f"cron step must be positive: {field!r}")
        if part in ("*", ""):
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        if start < lo or end > hi or start > end:
            raise ValueError(f"cron field out of range [{lo},{hi}]: {field!r}")
        out.update(range(start, end + 1, step))
    return out


def _tick_floor(now: datetime, *, minutes: int = 15) -> datetime:
    """The EventBridge tick this dispatcher run belongs to: ``now`` floored to the rule's
    quarter-hour boundary. The Fargate container starts ~30-90s AFTER the tick fires, so
    matching the cron against ``datetime.now()`` misses the tick minute every time (live
    2026-06-12: the 15:00 tick evaluated at 15:01:10 → ``*/15 * * * *`` never matched —
    "0 playbook run(s)"). Flooring recovers the scheduled minute regardless of start jitter;
    correct while startup latency stays under the 15-minute interval, and exact because the
    rule itself is quarter-aligned (infra ``cron(0/15 * * * ? *)``, #296)."""
    return now.replace(minute=(now.minute // minutes) * minutes, second=0, microsecond=0)


def cron_due(expr: str, now: datetime) -> bool:
    """True when the 5-field cron ``expr`` matches ``now`` (minute granularity).

    Malformed expressions return False (a broken cron must NOT fire every tick) — the validation
    layer rejects bad triggers at save time; this is a runtime safety net, logged once.
    """
    fields = expr.split()
    if len(fields) != 5:
        log.warning("ignoring malformed cron (need 5 fields): %r", expr)
        return False
    try:
        minute = _expand_field(fields[0], 0, 59)
        hour = _expand_field(fields[1], 0, 23)
        dom = _expand_field(fields[2], 1, 31)
        month = _expand_field(fields[3], 1, 12)
        # day-of-week: accept 0-7 (both 0 and 7 mean Sunday), then normalize 7 -> 0.
        dow = {d % 7 for d in _expand_field(fields[4], 0, 7)}
    except (ValueError, TypeError):
        log.warning("ignoring unparseable cron: %r", expr)
        return False
    cron_dow = (now.weekday() + 1) % 7  # Python Mon=0..Sun=6 -> cron Sun=0..Sat=6
    # Standard cron day matching: if BOTH day-of-month and day-of-week are restricted, a match on
    # EITHER fires; if one is "*" (full range), the other alone decides.
    dom_full = dom == set(range(1, 32))
    dow_full = dow == set(range(0, 7))
    if dom_full and dow_full:
        day_ok = True
    elif dow_full:
        day_ok = now.day in dom
    elif dom_full:
        day_ok = cron_dow in dow
    else:
        day_ok = now.day in dom or cron_dow in dow
    return now.minute in minute and now.hour in hour and now.month in month and day_ok


# --------------------------------------------------------------------------- #
# Dispatcher — finds matching ACTIVE playbooks and runs each via the injected
# ``run_playbook`` seam (so it is fully testable with a fake runner + store).
# --------------------------------------------------------------------------- #
class PlaybookDispatcher:
    """Fire a tenant's activated playbooks whose trigger matches.

    ``store`` is a tenant-scoped ``PlaybookStore`` (RLS upstream); ``run_playbook`` is
    ``(tenant_id, playbook_id, TriggerEvent) -> RunRecord`` — the runner seam, injected so the
    real per-tenant runtime resolution lives in ``main()`` and tests stay offline.
    """

    def __init__(self, store: Any,
                 run_playbook: Callable[[str, str, TriggerEvent], RunRecord]) -> None:
        self.store = store
        self.run_playbook = run_playbook

    @staticmethod
    def _trigger(definition: dict) -> dict:
        t = definition.get("trigger")
        return t if isinstance(t, dict) else {}

    def _active(self, tenant_id: str) -> list[dict]:
        return [r for r in self.store.list(tenant_id) if r.get("status") == STATUS_ACTIVE]

    def dispatch_scheduled(self, tenant_id: str, *, now: datetime | None = None) -> list[RunRecord]:
        """Run every ACTIVE schedule-playbook for ``tenant_id`` whose cron is due at ``now``."""
        now = now or datetime.now(timezone.utc)
        records: list[RunRecord] = []
        for row in self._active(tenant_id):
            trig = self._trigger(row["definition"])
            if trig.get("kind") != "schedule":
                continue
            cron = trig.get("schedule") or ""
            if not cron_due(cron, now):
                continue
            ev = TriggerEvent(kind="schedule", name=cron)
            records.append(self.run_playbook(tenant_id, row["id"], ev))
        return records

    def dispatch_event(self, tenant_id: str, event_name: str,
                       payload: dict | None = None) -> list[RunRecord]:
        """Run every ACTIVE event-playbook for ``tenant_id`` bound to ``event_name``."""
        records: list[RunRecord] = []
        for row in self._active(tenant_id):
            trig = self._trigger(row["definition"])
            if trig.get("kind") != "event" or trig.get("event") != event_name:
                continue
            ev = TriggerEvent(kind="event", name=event_name, payload=payload or {})
            records.append(self.run_playbook(tenant_id, row["id"], ev))
        return records


class BackgroundDispatcher:
    """Fire-and-forget wrapper for the in-process EVENT producers (POST /contacts,
    POST /deals): a user-facing create must never block on an agent run — one MA
    coordinator turn can take tens of seconds, far past request-timeout territory.

    ``dispatch_event`` spawns a contained daemon thread and returns ``[]`` immediately;
    the run's outcome is observable through the persisted run history (``playbook_runs``)
    and the Greenlight queue, never through the producer's request. Failures are logged
    and swallowed on the thread (the inner dispatcher already contains per-playbook
    failures; this contains dispatcher-level ones)."""

    def __init__(self, dispatcher: Any) -> None:
        self._dispatcher = dispatcher

    def dispatch_event(self, tenant_id: str, event_name: str,
                       payload: dict | None = None) -> list:
        import threading  # noqa: PLC0415 — stdlib, but keep module import surface minimal

        def _run() -> None:
            try:
                self._dispatcher.dispatch_event(tenant_id, event_name, payload)
            except Exception:  # noqa: BLE001 — a background run must die quietly, logged
                log.exception("background event dispatch failed for %s (tenant scoped)",
                              event_name)

        threading.Thread(target=_run, daemon=True,
                         name=f"playbook-event-{event_name}").start()
        return []


# --------------------------------------------------------------------------- #
# CLI — the EventBridge schedule target. Real mode (ANTHROPIC_API_KEY + DSN)
# wires PgPlaybookStore + a per-tenant Managed Agents runtime; offline runs
# against the in-memory store + FakeRuntime (a no-op safety net, lands nothing).
# --------------------------------------------------------------------------- #
ENV_DISPATCH_TENANTS = "PLAYBOOK_DISPATCH_TENANTS"

# Account states whose tenant is live enough to own (active) schedule playbooks. A row only gains
# a tenant_id once the Provisioner mints it (Step 55), so PROVISIONING/ACTIVE are exactly the
# already-tenanted, dispatchable rows; CREATED/…/PROVISIONING_FAILED carry no tenant_id and are
# filtered out by the `tenant_id IS NOT NULL` guard regardless.
_DISPATCHABLE_ACCOUNT_STATES = ("provisioning", "active")


def discover_db_tenants(dsn: str | None) -> list[str]:
    """Provisioned tenant_ids discovered from the RLS-EXEMPT `accounts` table (as crm_app).

    WHY accounts, not playbooks: the dispatcher runs as crm_app (NOBYPASSRLS), so it cannot
    enumerate the per-tenant `playbooks` table cross-tenant — FORCE'd RLS scopes a query to the
    single `app.current_tenant` GUC, and with none set it returns zero rows. The `accounts` table
    is the PRE-TENANT roster (deliberately RLS-exempt; crm_app may SELECT it — db/roles.sql), and
    every provisioned tenant has a row carrying its tenant_id. We discover that roster here;
    `dispatch_scheduled` then runs only each tenant's ACTIVE, due schedule playbooks (RLS-scoped
    per tenant), so a discovered tenant with no such playbook is a harmless no-op. A NEW SIGNUP
    appears here automatically the moment it is provisioned — no `PLAYBOOK_DISPATCH_TENANTS`
    tfvar edit, which is the whole point of this change.

    Inert + safe: no DSN -> []; any driver/query error is logged and yields [] (the caller still
    has the static env list to fall back on — discovery never crashes a dispatch run).
    """
    if not dsn:
        return []
    try:
        import psycopg2  # noqa: PLC0415 — guarded (import-safe module, mirrors _build_runner)
        from psycopg2.extras import RealDictCursor  # noqa: PLC0415

        conn = psycopg2.connect(dsn)
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                "SELECT DISTINCT tenant_id FROM accounts "
                "WHERE tenant_id IS NOT NULL AND status = ANY(%s)",
                (list(_DISPATCHABLE_ACCOUNT_STATES),),
            )
            rows = cur.fetchall()
            conn.commit()
        finally:
            conn.close()
        return list(dict.fromkeys(str(r["tenant_id"]) for r in rows))
    except Exception:  # noqa: BLE001 — discovery is best-effort; never crash the dispatch run
        log.exception("DB tenant discovery failed — falling back to the static env list")
        return []


def _resolve_tenants(args: argparse.Namespace, *, dsn: str | None = None,
                     discover: Callable[[str | None], list[str]] = discover_db_tenants
                     ) -> list[str]:
    """The tenants to dispatch this run.

      * explicit ``--tenant`` args WIN (operator override / tests) — used verbatim, no discovery.
      * otherwise the union of the static ``PLAYBOOK_DISPATCH_TENANTS`` env list (legacy/manual
        override, still honored so nothing already-covered regresses) and the DB-discovered roster
        (the new default — new signups picked up automatically). Deduped, order-stable.

    ``discover`` is injected so tests exercise the precedence/union logic with a fake roster and
    never touch a database; the default does the real RLS-exempt `accounts` read.
    """
    if args.tenant:
        return list(dict.fromkeys(t.strip() for t in args.tenant if t.strip()))
    raw = os.environ.get(ENV_DISPATCH_TENANTS, "")
    env_tenants = [t.strip() for t in raw.split(",") if t.strip()]
    db_tenants = discover(dsn)
    return list(dict.fromkeys([*env_tenants, *db_tenants]))


def _build_runner(dsn: str | None):
    """(store, run_playbook) for the CLI. Real mode resolves each tenant's persisted MA
    environment from the workspace store and runs the playbook against a runtime bound to it."""
    from shared.config import ENV_ANTHROPIC_API_KEY  # noqa: PLC0415

    api_key = os.environ.get(ENV_ANTHROPIC_API_KEY)
    if dsn and api_key:
        from agents.playbooks import runner as runner_mod  # noqa: PLC0415
        from agents.playbooks.store import PgPlaybookRunStore, PgPlaybookStore  # noqa: PLC0415
        from agents.runtime import get_runtime  # noqa: PLC0415
        from agents.workspace_store import PgWorkspaceStore  # noqa: PLC0415

        store = PgPlaybookStore(dsn)
        run_store = PgPlaybookRunStore(dsn)  # persist every scheduled-run digest (audit P0-2)
        workspaces = PgWorkspaceStore(dsn)

        def run_playbook(tenant_id: str, playbook_id: str, event: TriggerEvent) -> RunRecord:
            row = workspaces.get(tenant_id) or {}
            env_id = row.get("environment_id")
            if not env_id:
                return RunRecord(playbook_id=str(playbook_id), tenant_id=str(tenant_id),
                                 status="error", trigger={"kind": event.kind, "name": event.name},
                                 error="tenant not provisioned (no environment_id)")
            runtime = get_runtime({"runtime": "managed", "api_key": api_key,
                                   "environment_id": env_id})
            return runner_mod.run(runtime, store, tenant_id, playbook_id, event,
                                  environment_id=env_id, vault_id=row.get("vault_id"),
                                  run_store=run_store)

        return store, run_playbook

    # Offline: in-memory store (empty) + FakeRuntime — runs nothing real, never pages.
    from agents.playbooks import runner as runner_mod  # noqa: PLC0415
    from agents.playbooks.store import InMemoryPlaybookStore  # noqa: PLC0415
    from agents.runtime import get_runtime  # noqa: PLC0415

    store = InMemoryPlaybookStore()

    def run_playbook(tenant_id: str, playbook_id: str, event: TriggerEvent) -> RunRecord:
        return runner_mod.run(get_runtime({"runtime": "fake"}), store,
                              tenant_id, playbook_id, event)

    return store, run_playbook


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m agents.playbooks.dispatch",
        description="Fire activated playbooks whose trigger is due (the EventBridge schedule target).",
    )
    p.add_argument("--schedule", action="store_true",
                   help="run every due schedule-playbook for the resolved tenants")
    p.add_argument("--tenant", action="append", metavar="TENANT_ID",
                   help=f"tenant to dispatch (repeatable); default = ${ENV_DISPATCH_TENANTS}")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = _parser().parse_args(argv)
    if not args.schedule:
        log.error("no dispatch mode selected (use --schedule)")
        return 2

    from shared.config import dsn_from_env  # noqa: PLC0415

    dsn = dsn_from_env()
    tenants = _resolve_tenants(args, dsn=dsn)
    if not tenants:
        log.warning("no tenants to dispatch (DB discovery + %s both empty) — nothing to do",
                    ENV_DISPATCH_TENANTS)
        return 0

    store, run_playbook = _build_runner(dsn)
    dispatcher = PlaybookDispatcher(store, run_playbook)
    # Match against the TICK time, not container-start time (see _tick_floor — startup
    # jitter otherwise misses the scheduled minute on every single run).
    now = _tick_floor(datetime.now(timezone.utc))
    total = 0
    for tenant_id in tenants:
        try:
            records = dispatcher.dispatch_scheduled(tenant_id, now=now)
        except Exception:  # noqa: BLE001 — one tenant must not stop the rest
            log.exception("tenant %s: schedule dispatch failed", tenant_id)
            continue
        total += len(records)
        for r in records:
            log.info("tenant %s: ran playbook %s -> %s", tenant_id, r.playbook_id, r.status)
    log.info("dispatch complete: %d playbook run(s) across %d tenant(s)", total, len(tenants))
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via main() in tests
    raise SystemExit(main())
