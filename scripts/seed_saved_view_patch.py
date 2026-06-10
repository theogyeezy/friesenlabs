#!/usr/bin/env python3
"""Patch the demo tenant with the missing `demo_pipeline` saved view (issue #125).

WHY: `scripts/seed_demo_tenant.py` wipes `saved_views` in its delete loop but never inserts a
row, so the Command Center's default fetch (`GET /views/demo_pipeline`, see
web/src/api/DashboardView.tsx) 404s and the demo tenant renders "No saved views yet". This
one-off inserts exactly one sensible saved view so the saved-views surface is verifiable.

SAFE + IDEMPOTENT:
  - The spec below is validated IN THIS SCRIPT against shared/schemas/view_spec.schema.json
    (via shared.view_spec.validate) AND against the real Cube catalog members defined in
    semantic/model/cubes/ — an invalid spec aborts before any DB write.
  - One guarded INSERT (`WHERE NOT EXISTS` on this tenant's `demo_pipeline`, RLS-scoped):
    rerunning is a no-op. It never deletes or updates existing rows.
  - Connects as crm_app and runs inside ONE transaction that begins with
    `SET LOCAL app.current_tenant`, so the write itself exercises the RLS isolation path
    and the GUC can never leak past the transaction.

RUN (Lane Nick — live mutation):
  One-off ECS task (uplift-migrate-oneoff family / api task-def container override; the api
  image already ships scripts/ + shared/):
      command: ["python", "scripts/seed_saved_view_patch.py"]   env: TENANT_ID=<demo uuid>
  Or locally against the DB:
      UPLIFT_DB_URL=postgresql://crm_app:***@host:5432/uplift TENANT_ID=<uuid> \
          python scripts/seed_saved_view_patch.py

Import-safe: importing this module performs no I/O (tests import it offline).
"""
from __future__ import annotations

import json
import os
import sys

# Make `shared` importable when run as `python scripts/seed_saved_view_patch.py` from the
# repo root or from /app in the api image (mirrors conftest.py's path setup).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared import view_spec  # noqa: E402

VIEW_ID = "demo_pipeline"  # MUST match web/src/api/DashboardView.tsx's default viewId.

# Real members from semantic/model/cubes/deals.js — NOT the web sample's `Deals.totalValue`,
# which does not exist in the Cube catalog. tests/unit/test_seed_saved_view_patch.py keeps
# this set in sync with the model files.
CUBE_MEMBERS = {
    "Deals.count", "Deals.pipeline_value", "Deals.avg_deal_size",
    "Deals.title", "Deals.stage", "Deals.currency", "Deals.created_at",
}

# SPEC, NOT CODE (hard constraint #7): declarative, catalog components only.
SPEC = {
    "view_id": VIEW_ID,
    "title": "Pipeline overview",
    "version": 1,
    "source_prompt": "Show me total pipeline and value by stage",
    "semantic_refs": ["Deals.pipeline_value", "Deals.count", "Deals.stage"],
    "layout": [
        {"type": "kpi", "title": "Open pipeline", "metric": "Deals.pipeline_value"},
        {"type": "kpi", "title": "Open deals", "metric": "Deals.count"},
        {
            "type": "chart",
            "title": "Pipeline value by stage",
            "encoding": "vega-lite",
            "spec": {
                "mark": "bar",
                "encoding": {
                    "x": {"field": "stage", "type": "nominal", "title": "Stage"},
                    "y": {"field": "value", "type": "quantitative", "title": "Value"},
                },
            },
            "query": {"measures": ["Deals.pipeline_value"], "dimensions": ["Deals.stage"]},
        },
        {
            "type": "table",
            "title": "Deals by stage",
            "query": {
                "measures": ["Deals.count", "Deals.pipeline_value"],
                "dimensions": ["Deals.stage"],
            },
        },
    ],
}

# Guarded insert: the subquery is RLS-scoped to app.current_tenant, so "exists" means
# "this tenant already has any version of demo_pipeline" — rerunning is a no-op.
INSERT_SQL = (
    "INSERT INTO saved_views (tenant_id, view_id, version, spec_json, semantic_refs, "
    "source_prompt, created_by) "
    "SELECT %(tenant)s, %(view_id)s, 1, %(spec)s, %(refs)s, %(prompt)s, %(created_by)s "
    "WHERE NOT EXISTS (SELECT 1 FROM saved_views WHERE view_id = %(view_id)s)"
)


def _connect():
    """Build a crm_app connection: UPLIFT_DB_URL if set, else the seed's Secrets Manager path."""
    import psycopg2  # noqa: PLC0415 — lazy: import-safety for offline tests

    dsn = os.environ.get("UPLIFT_DB_URL")
    if dsn:
        return psycopg2.connect(dsn)
    import boto3  # noqa: PLC0415

    sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    crm = json.loads(sm.get_secret_value(SecretId=os.environ["CRM_APP_SECRET_ARN"])["SecretString"])
    return psycopg2.connect(host=os.environ["DB_HOST"], port=5432,
                            dbname=os.environ.get("DB_NAME", "uplift"),
                            user=crm["username"], password=crm["password"])


def main(connect=None) -> int:
    """Validate the spec, then idempotently insert it for TENANT_ID. Returns 0 on success."""
    tenant = os.environ["TENANT_ID"]

    # Never write an invalid spec (schema + real-catalog members) — abort loudly instead.
    view_spec.validate(SPEC, allowed_members=CUBE_MEMBERS)

    conn = (connect or _connect)()
    try:
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute("SET LOCAL app.current_tenant = %s", (tenant,))
        cur.execute(INSERT_SQL, {
            "tenant": tenant,
            "view_id": VIEW_ID,
            "spec": json.dumps(SPEC),
            "refs": json.dumps(SPEC["semantic_refs"]),
            "prompt": SPEC["source_prompt"],
            "created_by": "seed_saved_view_patch",
        })
        inserted = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if inserted:
        print(f"seeded saved view '{VIEW_ID}' (version 1) for tenant {tenant}")
    else:
        print(f"saved view '{VIEW_ID}' already present for tenant {tenant} — no-op")
    return 0


if __name__ == "__main__":
    sys.exit(main())
