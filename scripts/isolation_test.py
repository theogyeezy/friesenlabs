#!/usr/bin/env python3
"""Multi-tenant isolation test — the cross-cutting gate run after ANY data/agent/auth change.

It proves that with Postgres RLS FORCEd and a *non-owner* app role, tenant A can never read
tenant B's rows (Build Guide red box: RLS silently fails if not forced / connected as owner).

Runnable now:
  - If UPLIFT_DB_URL is set, it runs the real two-tenant check against `documents`.
  - Otherwise it reports the data plane isn't up yet and exits 0 (nothing to isolate).
Once Phase 1 lands, point UPLIFT_DB_URL at the app role and this becomes a hard gate.

CI hard-gate: set UPLIFT_REQUIRE_DB=1 to turn the graceful "PENDING" skip into a FAILURE.
In CI a real Postgres is provisioned and the gate MUST run — if no DB is reachable (or
UPLIFT_DB_URL is unset / psycopg2 missing) the gate would otherwise silently pass and never
gate. With the flag set, those conditions exit non-zero. Locally/dev (flag unset) the skip stays
graceful so the harness is still runnable without a DB.

Usage:
  UPLIFT_DB_URL=postgresql://uplift_app:***@host:5432/uplift python scripts/isolation_test.py
  UPLIFT_REQUIRE_DB=1 UPLIFT_DB_URL=... python scripts/isolation_test.py   # CI hard gate
"""
from __future__ import annotations

import os
import sys
import uuid

DB_URL = os.environ.get("UPLIFT_DB_URL")
REQUIRE_DB = os.environ.get("UPLIFT_REQUIRE_DB") == "1"


def _pending(msg: str) -> int:
    # In CI (UPLIFT_REQUIRE_DB=1) there is no excuse for "no DB to test": a missing/unreachable
    # data plane means the gate can't prove isolation, so FAIL instead of the clean skip.
    if REQUIRE_DB:
        print(f"[isolation] FAIL — UPLIFT_REQUIRE_DB=1 but {msg}")
        print("[isolation] a real Postgres must be reachable in CI for the isolation gate to run.")
        return 1
    print(f"[isolation] PENDING — {msg}")
    print("[isolation] no data plane to test yet; exiting clean (set UPLIFT_DB_URL once Phase 1 lands).")
    return 0


def main() -> int:
    if not DB_URL:
        return _pending("UPLIFT_DB_URL not set")
    try:
        import psycopg2
    except ImportError:
        return _pending("psycopg2 not installed")

    try:
        conn = psycopg2.connect(DB_URL)
    except Exception as e:  # noqa: BLE001
        return _pending(f"cannot connect to DB ({e.__class__.__name__})")

    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    vec = "[" + ",".join(["0.1"] * 1024) + "]"
    failures: list[str] = []
    with conn:
        with conn.cursor() as cur:
            # GUC name MUST match the policy in db/schema.sql: app.current_tenant.
            cur.execute("SET app.current_tenant = %s", (tenant_a,))
            cur.execute(
                "INSERT INTO documents (tenant_id, source, content, embedding) "
                "VALUES (%s,'test','a-secret',%s)",
                (tenant_a, vec),
            )
            cur.execute("SET app.current_tenant = %s", (tenant_b,))
            cur.execute(
                "INSERT INTO documents (tenant_id, source, content, embedding) "
                "VALUES (%s,'test','b-secret',%s)",
                (tenant_b, vec),
            )
            # As tenant A, count rows — RLS should hide tenant B's.
            cur.execute("SET app.current_tenant = %s", (tenant_a,))
            cur.execute("SELECT count(*) FROM documents WHERE content='b-secret'")
            leaked = cur.fetchone()[0]
            if leaked != 0:
                failures.append(f"tenant A saw {leaked} of tenant B's rows — RLS NOT enforced")
            # As tenant A, a vector ANN query must never surface tenant B's row.
            try:
                cur.execute("SET hnsw.iterative_scan = 'relaxed_order'")
            except Exception:  # noqa: BLE001 — setting may not exist on older pgvector
                conn.rollback()
                cur.execute("SET app.current_tenant = %s", (tenant_a,))
            cur.execute(
                "SELECT content FROM documents ORDER BY embedding <=> %s::vector LIMIT 50", (vec,)
            )
            if any(r[0] == "b-secret" for r in cur.fetchall()):
                failures.append("vector query returned tenant B's row — RLS NOT enforced on ANN")
            # And cannot UPDATE across tenants.
            cur.execute("UPDATE documents SET content='hacked' WHERE content='b-secret'")
            if cur.rowcount != 0:
                failures.append("tenant A could UPDATE tenant B's rows — RLS NOT enforced")
            conn.rollback()  # never persist test rows

    if failures:
        for f in failures:
            print(f"[isolation] FAIL — {f}")
        return 1
    print("[isolation] PASS — RLS enforced; no cross-tenant read/write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
