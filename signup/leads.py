"""Lead capture store — the `leads` table behind POST /public/leads (api/public_routes.py).

PRE-TENANT by nature (a lead precedes any account or tenant), so like accounts/stripe_events the
table is RLS-EXEMPT (db/schema.sql comment) and the store issues NO ``SET LOCAL
app.current_tenant``. Connection discipline rides signup/store_pg.py's shared ``_PgBase``
(non-owner crm_app role, pooled per-op conns, one transaction per op). Import-safe: psycopg2 is
imported lazily on construction; the in-memory store needs nothing.
"""
from __future__ import annotations

import uuid

from .store_pg import _PgBase


class PgLeadStore(_PgBase):
    """Aurora-backed lead sink (as crm_app). The route validates/caps BEFORE insert."""

    def insert(self, *, kind: str, name: str, email: str, message: str | None = None,
               company: str | None = None, source_ip: str | None = None) -> str:
        lead_id = str(uuid.uuid4())
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO leads (id, kind, name, email, message, company, source_ip) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (lead_id, kind, name, email, message, company, source_ip),
            )
        return lead_id


class MemoryLeadStore:
    """In-memory stand-in (tests / local dev) — same insert contract."""

    def __init__(self):
        self.rows: list[dict] = []

    def insert(self, *, kind: str, name: str, email: str, message: str | None = None,
               company: str | None = None, source_ip: str | None = None) -> str:
        lead_id = str(uuid.uuid4())
        self.rows.append({"id": lead_id, "kind": kind, "name": name, "email": email,
                          "message": message, "company": company, "source_ip": source_ip})
        return lead_id
