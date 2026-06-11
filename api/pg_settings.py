"""PgSettingsStore — persisted workspace settings over the EXISTING `tenant_settings` table.

Backs the GET/PUT /account/settings surface (api/settings_routes.py): one row per tenant carrying
`workspace_name` (display name) + `notification_prefs` (a flat jsonb bag of bool/string prefs). The
columns are appended idempotently in db/schema.sql; the table is already FORCE'd RLS with a
tenant_isolation policy (autonomy_level/killswitch_engaged live on the same row), so this store adds
no new policy — it only reads/writes the two new columns.

RLS DISCIPLINE: every op REUSES the `_PgTenantClient` plumbing from api/pg_clients.py (imported, not
re-implemented) — a pooled per-op connection in ONE transaction that begins with
`SET LOCAL app.current_tenant` (auto-resets at txn end), connecting as the non-owner crm_app role.
Postgres RLS scopes every read/write; there is NO hand-written `WHERE tenant_id = ...` for tenancy.

THE TRUST RULE: `tenant_id` flows in from the caller (the verified Cognito JWT claim threaded by the
route) — never from env, headers, or payloads here.

UPSERT SEMANTICS: `upsert` is `INSERT .. ON CONFLICT (tenant_id) DO UPDATE` and updates ONLY the
fields the caller provided (a None field is left untouched on UPDATE / defaulted on INSERT). Like
`PgControlSettingsStore.set_killswitch`, a settings write is an explicit user action that MUST win
over the provisioning seed's `DO NOTHING` row — so it DO UPDATEs, never DO NOTHING.

Import-safe: psycopg2 is imported lazily by `_PgTenantClient` (DSN path only) — importing this module
needs no network, AWS, or psycopg2.
"""
from __future__ import annotations

import json
from typing import Any

from api.pg_clients import _PgTenantClient, _dict_one


def _normalize_prefs(value: Any) -> dict:
    """Coerce the stored notification_prefs to a plain dict for the wire.

    psycopg2 returns a jsonb column as an already-decoded dict; a fake/raw cursor may hand back a
    JSON string. None (column default never NULL, but defensive) -> {}.
    """
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return dict(value) if isinstance(value, dict) else {}


def _row_out(row: dict) -> dict:
    """One settings row in the wire shape: {workspace_name, notification_prefs}."""
    return {
        "workspace_name": row.get("workspace_name"),
        "notification_prefs": _normalize_prefs(row.get("notification_prefs")),
    }


class PgSettingsStore(_PgTenantClient):
    """Workspace settings (workspace_name + notification_prefs) over `tenant_settings`, RLS-scoped.

    Construct with EITHER a `dsn` (a pool is built; psycopg2 imported lazily) OR a `conn_factory`
    (zero-arg callable -> a DB-API connection per op) — exactly like every store in api/pg_clients.py.
    """

    def get(self, tenant_id) -> dict | None:
        """The tenant's settings row (None when not yet seeded). RLS-scoped via SET LOCAL.

        Returns {workspace_name, notification_prefs}; workspace_name may be None until first saved.
        """
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT workspace_name, notification_prefs "
                "FROM tenant_settings WHERE tenant_id = %s",
                (str(tenant_id),),
            )
            row = _dict_one(cur)
        if row is None:
            return None
        return _row_out(row)

    def upsert(self, tenant_id, *, workspace_name: str | None = None,
               notification_prefs: dict | None = None) -> dict:
        """Upsert the provided settings fields and RETURN the saved row.

        ON CONFLICT (tenant_id) DO UPDATE updates ONLY the fields the caller passed (a None field is
        left untouched on the UPDATE arm — `COALESCE(EXCLUDED.col, tenant_settings.col)`). A settings
        write MUST win over the provisioning-seeded row, like PgControlSettingsStore.set_killswitch.

        At least one of workspace_name / notification_prefs must be provided (the route validates the
        body; this is a defensive guard so an empty upsert is never a silent no-op).
        """
        if workspace_name is None and notification_prefs is None:
            raise ValueError("upsert requires at least one of workspace_name / notification_prefs")
        # notification_prefs is bound as a ::jsonb param (serialized once here; None stays NULL so the
        # COALESCE keeps the existing value). workspace_name binds as text.
        prefs_param = None if notification_prefs is None else json.dumps(notification_prefs)
        with self._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO tenant_settings (tenant_id, workspace_name, notification_prefs) "
                "VALUES (%s, %s, COALESCE(%s::jsonb, '{}'::jsonb)) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "workspace_name = COALESCE(EXCLUDED.workspace_name, tenant_settings.workspace_name), "
                "notification_prefs = COALESCE(%s::jsonb, tenant_settings.notification_prefs) "
                "RETURNING workspace_name, notification_prefs",
                (str(tenant_id), workspace_name, prefs_param, prefs_param),
            )
            row = _dict_one(cur)
        if row is None:
            raise RuntimeError("settings upsert returned no row")
        return _row_out(row)


__all__ = ["PgSettingsStore"]
