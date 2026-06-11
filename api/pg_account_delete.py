"""Tenant-scoped Postgres account teardown — the destructive sibling of the RAG/CRM reads.

`PgAccountDeleter` is the real Pg deleter behind POST /account/delete (api/account_delete_routes.py).
It reuses the FIXED RLS plumbing from `api/pg_clients.py` by SUBCLASSING `_PgTenantClient` (pool /
conn-factory + the per-op `SET LOCAL app.current_tenant` transaction). It connects as the NON-OWNER
`crm_app` role and never writes a hand-written `WHERE tenant_id = ...`: RLS scopes every DELETE to
the calling tenant inside the per-op transaction. THE TRUST RULE — the tenant_id is the verified
Cognito claim threaded in by the route; it is never read from env, headers, or a request body here.

WHAT IT DELETES vs WHAT IT RETAINS (grounded in db/schema.sql + db/roles.sql, not guessed):
  * MUTABLE tenant data — DELETE is GRANTed to crm_app, so a hard DELETE succeeds and is the right
    teardown: `activities`, `deals`, `contacts`, `companies`, `documents`, `saved_views`,
    `playbooks`. Ordered child-before-parent so same-tenant FKs (activities->deals/contacts,
    deals/contacts->companies) never block a delete.
  * APPEND-ONLY / audit tables — db/roles.sql `REVOKE DELETE`s crm_app on them, so a hard DELETE
    WOULD FAIL. The teardown SKIPS them and reports them as retained-with-reason:
    `onboarding_state`, `usage_counters`, `cost_events`, `support_requests` (the task's list) plus
    the rest of the append-only surface (`traces`, `approvals`, `predictions`, `tenant_settings`,
    `tenant_workspaces`, `workspace_keys`, `leads`, `ingest_cursor`). Audit history outlives the
    tenant by design.

SAFETY:
  * ONE transaction, a SAVEPOINT per table: a single table's failure ROLLBACK TO its savepoint and
    is reported under `failed` — it never leaves a half-teardown, and the rest of the teardown still
    commits. Idempotent: a re-run finds nothing and reports 0s.
  * `documents.id` is `bigint`; the rest are uuid PKs — irrelevant to a `DELETE FROM <table>` with
    no predicate (RLS supplies the only filter), so one fixed-SQL deleter covers every table.

Import-safe: subclassing `_PgTenantClient` pulls in no psycopg2/AWS at import (the parent imports
psycopg2 lazily only when constructed with a `dsn`). Tests inject a `conn_factory`.
"""
from __future__ import annotations

from api.pg_clients import _PgTenantClient

# Mutable tenant tables the teardown HARD-DELETEs. crm_app holds DELETE on every one of these
# (db/roles.sql: the `documents, companies, contacts, deals, activities, saved_views` core grant +
# the explicit `GRANT ... DELETE ON playbooks`). Ordered CHILD-BEFORE-PARENT so the same-tenant FKs
# (activities -> deals/contacts, deals/contacts -> companies) never block a delete mid-teardown.
DELETABLE_TABLES: tuple[str, ...] = (
    "activities",
    "deals",
    "contacts",
    "companies",
    "documents",
    "saved_views",
    "playbooks",
)

# Append-only / audit tables: db/roles.sql REVOKEs DELETE from crm_app on each, so a hard DELETE
# would error. The teardown SKIPS them and reports each as retained-with-reason. The task's required
# four (onboarding_state, usage_counters, cost_events, support_requests) lead; the rest of the
# append-only surface follows so the report is honest about everything that outlives the tenant.
RETAINED_TABLES: dict[str, str] = {
    "onboarding_state": "append-only (REVOKE DELETE on crm_app) — first-run state is upserted, never erased",
    "usage_counters": "append-only (REVOKE DELETE on crm_app) — billing-period usage record",
    "cost_events": "append-only (REVOKE DELETE on crm_app) — immutable token-cost audit, like traces",
    "support_requests": "append-only (REVOKE DELETE on crm_app) — contact/help intake is never erased",
    "traces": "append-only audit trail (REVOKE DELETE on crm_app) — decision traces are never erased",
    "approvals": "audit trail (REVOKE DELETE on crm_app) — Greenlight decision history is never erased",
    "predictions": "append-only (REVOKE DELETE on crm_app) — Cortex prediction log is immutable audit",
    "tenant_settings": "control row (REVOKE DELETE on crm_app) — kill switch / autonomy is flipped, never deleted",
    "tenant_workspaces": "control row (REVOKE DELETE on crm_app) — agent-plane ids are re-upserted, never deleted",
    "workspace_keys": "append-only (REVOKE DELETE on crm_app) — key-allocation audit trail",
    "leads": "append-only (REVOKE UPDATE/DELETE on crm_app) — captured leads are never erased",
    "ingest_cursor": "ingestion bookkeeping — retained so a re-ingest resumes cleanly, not tenant content",
}


class PgAccountDeleter(_PgTenantClient):
    """Hard-deletes a tenant's MUTABLE data inside one RLS-scoped, savepoint-guarded transaction.

    Construct with a `dsn` (real pool) or a `conn_factory` (tests) — same contract as every other
    `_PgTenantClient`. `delete_tenant_data(tenant_id=...)` returns the structured teardown report
    `{deleted: {table: count}, retained: {table: reason}, failed: {table: error}}`.
    """

    def delete_tenant_data(self, *, tenant_id: str) -> dict:
        """Tear down the tenant's mutable data. RLS (via the parent's `SET LOCAL`) is the ONLY
        tenant filter — there is no hand-written `WHERE tenant_id`, so a misbound transaction can
        never reach across tenants. Each table runs under its own SAVEPOINT: a per-table failure
        rolls back only that table and is reported under `failed`; the rest still commits.

        Idempotent: tables already emptied report 0. The append-only tables are never touched —
        they are reported under `retained` with the schema/roles reason they are kept.
        """
        deleted: dict[str, int] = {}
        failed: dict[str, str] = {}

        with self._tx(tenant_id) as cur:
            for table in DELETABLE_TABLES:
                # table comes from the hand-written allow-list above, never from input — safe to
                # interpolate. RLS supplies the tenant predicate; we add none.
                savepoint = f"sp_{table}"
                cur.execute(f"SAVEPOINT {savepoint}")
                try:
                    cur.execute(f"DELETE FROM {table}")
                    # rowcount is the count of rows the DELETE removed (RLS-scoped to this tenant).
                    count = cur.rowcount
                    deleted[table] = int(count) if count is not None and count >= 0 else 0
                    cur.execute(f"RELEASE SAVEPOINT {savepoint}")
                except Exception as exc:  # noqa: BLE001 — isolate the failing table, keep the rest
                    # Roll back ONLY this table's statement; the outer transaction stays alive so the
                    # other tables can still commit. Report the table as failed (type, not raw text,
                    # to avoid leaking DSN/value detail into the API response).
                    cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    failed[table] = type(exc).__name__

        return {
            "deleted": deleted,
            "retained": dict(RETAINED_TABLES),
            "failed": failed,
        }
