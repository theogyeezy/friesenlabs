"""Load the committed demo-tenant fixture into one tenant — the Option B loader.

Pairs with `scripts/generate_demo_dataset.py` (the generator) and its committed output
`scripts/demo/fixture/demo_tenant.json`. The generator fabricates the "Meridian Mechanical
Group" CRM universe deterministically (zero PII, `.example` domains, 555-01XX phones); THIS
script is the idempotent loader that lands that fixture in Postgres for a live demo.

What it does (the ratified brief, docs/decisions/demo-tenant-synthetic-dataset.md):
  * Connects as the RLS-bound NON-OWNER `crm_app` role and runs everything inside ONE
    transaction that begins with `SET LOCAL app.current_tenant = <tenant>` — so every
    DELETE/INSERT is tenant-scoped by the `tenant_isolation` policy and the GUC auto-resets
    at COMMIT. NEVER the table owner; NEVER a session-level SET that could leak. This is the
    exact pattern the app stores use (api/pg_clients.py, ingest/pipeline.py PgDocumentStore).
  * Idempotent: wipes this tenant's CRM rows + saved_views, and the fixture's own documents
    (`ref_id LIKE 'demo:doc:%'`), then re-inserts. Re-running resets state without duplicating
    — and the demo:doc: scope leaves a separately-seeded knowledge corpus (demo:kb:%, see
    scripts/demo/seed_knowledge.py) untouched.
  * Embeds the `documents` corpus at load time through the SAME ingest embedder seam
    (ingest.run_sync.build_embedder): the deterministic offline stub by default (so tests and
    dry-runs are $0 / need no AWS), Titan V2 only when INGEST_REAL_STORES=1 is set.

Run INSIDE the VPC as a one-off ECS task (uplift-migrate-oneoff family, infra/RUNBOOK.md):
    TENANT_ID=<uuid> INGEST_REAL_STORES=1 \
      python scripts/demo/load_demo_tenant.py
or against any reachable crm_app DSN locally:
    python scripts/demo/load_demo_tenant.py --dsn postgresql://crm_app:...@host/uplift \
      --tenant <uuid>

Connection resolution (first that is configured wins):
  1. --dsn / UPLIFT_DB_URL                  — a full crm_app DSN
  2. CRM_APP_SECRET_ARN + DB_HOST [+ DB_NAME]  — Secrets Manager (the ECS one-off path,
                                                 mirrors scripts/seed_demo_tenant.py)

Tenant resolution: --tenant, else TENANT_ID, else the fixture's meta.tenant_id (the fixed
demo uuid). The fixture rows carry no tenant_id — the loader stamps the chosen one — so the
same fixture loads under any tenant id you point it at.

IMPORT SAFETY: importing this module needs no AWS, boto3, or psycopg2; the real clients are
constructed lazily, only inside the connect/embed paths in real use.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# The committed fixture (generator output) — the single source of truth for what loads.
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_FIXTURE = os.path.join(HERE, "fixture", "demo_tenant.json")

# documents wipe is scoped to the FIXTURE's own ref_id namespace, not all of demo:%, so a
# separately-seeded knowledge corpus (demo:kb:%) survives a CRM reset.
FIXTURE_DOC_REF_PREFIX = "demo:doc:"

# Child-before-parent delete order (FKs: activities -> deals/contacts, deals -> companies/
# contacts, contacts -> companies). saved_views is FK-free. approvals are NOT wiped: the audit
# trail is append-only (db/roles.sql revokes crm_app DELETE on approvals) — fixture approvals
# carry fixed ids and are idempotently upserted instead (INSERT .. ON CONFLICT (id) DO UPDATE).
WIPE_TABLES = ("activities", "deals", "contacts", "companies", "saved_views")


# --------------------------------------------------------------------------- fixture
def read_fixture(path: str = DEFAULT_FIXTURE) -> dict:
    """Load and lightly sanity-check the committed demo fixture JSON."""
    with open(path, encoding="utf-8") as f:
        dataset = json.load(f)
    for key in ("companies", "contacts", "deals", "activities", "approvals",
                "saved_views", "documents"):
        if key not in dataset:
            raise ValueError(f"fixture {path} missing required section {key!r}")
    return dataset


def build_embedder():
    """The ingest embedder seam: offline deterministic stub by default, Titan V2 only when
    INGEST_REAL_STORES=1 (ingest.run_sync.build_embedder owns that switch). Reused, not
    reinvented — the demo corpus embeds through the exact path production ingestion uses."""
    from ingest.run_sync import build_embedder as _build  # noqa: PLC0415 — lazy (boto3 in real mode)

    return _build()


# --------------------------------------------------------------------------- load
def _vector_literal(embedding) -> str:
    """pgvector text format ('[0.1,0.2,...]'); each element float-coerced (mirrors
    api/pg_clients._vector_literal / ingest PgDocumentStore.upsert)."""
    values = [float(x) for x in embedding]
    if not values:
        raise ValueError("embedder returned an empty vector")
    return "[" + ",".join(str(v) for v in values) + "]"


def load(conn, dataset: dict, *, tenant_id: str, embedder) -> dict:
    """Load `dataset` into `conn` (an open crm_app connection) under `tenant_id`, in ONE
    tenant-scoped transaction. Wipe-then-insert => idempotent. Returns row counts.

    `conn` must be a non-owner (crm_app) connection so RLS applies; `embedder` is `str ->
    1024-float vector`. Commits on success, rolls back on error.
    """
    from psycopg2.extras import Json  # noqa: PLC0415 — lazy (import-safe module)

    tenant = str(tenant_id)
    cur = conn.cursor()
    try:
        # SET LOCAL binds the GUC to THIS transaction only (auto-resets at COMMIT/ROLLBACK).
        cur.execute("SET LOCAL app.current_tenant = %s", (tenant,))

        # --- wipe (RLS scopes every DELETE to this tenant) -------------------------------
        for table in WIPE_TABLES:
            cur.execute(f"DELETE FROM {table}")  # noqa: S608 — table from a fixed allow-list
        cur.execute("DELETE FROM documents WHERE ref_id LIKE %s",
                    (FIXTURE_DOC_REF_PREFIX + "%",))

        # --- companies / contacts / deals / activities -----------------------------------
        for c in dataset["companies"]:
            cur.execute(
                "INSERT INTO companies (id, tenant_id, name, domain, ref_id, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (c["id"], tenant, c["name"], c.get("domain"), c.get("ref_id"),
                 c.get("created_at")))
        for c in dataset["contacts"]:
            cur.execute(
                "INSERT INTO contacts (id, tenant_id, company_id, name, email, phone, ref_id, "
                "created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (c["id"], tenant, c.get("company_id"), c.get("name"), c.get("email"),
                 c.get("phone"), c.get("ref_id"), c.get("created_at")))
        for d in dataset["deals"]:
            cur.execute(
                "INSERT INTO deals (id, tenant_id, company_id, contact_id, title, stage, amount, "
                "currency, ref_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (d["id"], tenant, d.get("company_id"), d.get("contact_id"), d.get("title"),
                 d.get("stage"), d.get("amount"), d.get("currency", "USD"), d.get("ref_id"),
                 d.get("created_at")))
        for a in dataset["activities"]:
            cur.execute(
                "INSERT INTO activities (id, tenant_id, contact_id, deal_id, kind, body, "
                "occurred_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (a["id"], tenant, a.get("contact_id"), a.get("deal_id"), a.get("kind"),
                 a.get("body"), a.get("occurred_at")))

        # --- approvals (proposed_action jsonb; decided rows carry decided_by/at) ---------
        # Upsert, not wipe-then-insert: approvals are an append-only audit trail (crm_app has
        # no DELETE — db/roles.sql), so idempotency rides the fixture's fixed ids instead.
        for ap in dataset["approvals"]:
            cur.execute(
                "INSERT INTO approvals (id, tenant_id, proposed_action, agent, reasoning, "
                "value_at_stake, status, decided_by, deny_message, created_at, decided_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (id) DO UPDATE SET "
                "proposed_action=EXCLUDED.proposed_action, agent=EXCLUDED.agent, "
                "reasoning=EXCLUDED.reasoning, value_at_stake=EXCLUDED.value_at_stake, "
                "status=EXCLUDED.status, decided_by=EXCLUDED.decided_by, "
                "deny_message=EXCLUDED.deny_message, created_at=EXCLUDED.created_at, "
                "decided_at=EXCLUDED.decided_at",
                (ap["id"], tenant, Json(ap["proposed_action"]), ap.get("agent"),
                 ap.get("reasoning"), ap.get("value_at_stake"), ap.get("status", "pending"),
                 ap.get("decided_by"), ap.get("deny_message"), ap.get("created_at"),
                 ap.get("decided_at")))

        # --- saved_views (spec_json + semantic_refs jsonb) -------------------------------
        for sv in dataset["saved_views"]:
            cur.execute(
                "INSERT INTO saved_views (id, tenant_id, view_id, version, spec_json, "
                "semantic_refs, source_prompt, created_by, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (sv["id"], tenant, sv["view_id"], sv.get("version", 1), Json(sv["spec_json"]),
                 Json(sv.get("semantic_refs")), sv.get("source_prompt"), sv.get("created_by"),
                 sv.get("created_at")))

        # --- documents (embedded at load time via the ingest embedder seam) --------------
        embedded = 0
        for doc in dataset["documents"]:
            vec = _vector_literal(embedder(doc["content"]))
            cur.execute(
                "INSERT INTO documents (tenant_id, source, ref_id, content, embedding, "
                "created_at) VALUES (%s,%s,%s,%s,%s::vector,%s) "
                "ON CONFLICT (tenant_id, source, ref_id) "
                "DO UPDATE SET content=EXCLUDED.content, embedding=EXCLUDED.embedding",
                (tenant, doc.get("source"), doc.get("ref_id"), doc.get("content"), vec,
                 doc.get("created_at")))
            embedded += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "companies": len(dataset["companies"]),
        "contacts": len(dataset["contacts"]),
        "deals": len(dataset["deals"]),
        "activities": len(dataset["activities"]),
        "approvals": len(dataset["approvals"]),
        "saved_views": len(dataset["saved_views"]),
        "documents": embedded,
    }


# --------------------------------------------------------------------------- connection
def _dsn_from_env(cli_dsn: str | None) -> str | None:
    """A full crm_app DSN from --dsn or UPLIFT_DB_URL (None if neither set)."""
    return cli_dsn or os.environ.get("UPLIFT_DB_URL") or None


def connect_crm_app(cli_dsn: str | None = None):
    """Open a crm_app (non-owner, RLS-bound) connection from the configured source.

    Preference: an explicit DSN (--dsn / UPLIFT_DB_URL), else Secrets Manager
    (CRM_APP_SECRET_ARN + DB_HOST), the ECS one-off path that mirrors seed_demo_tenant.py.
    psycopg2 / boto3 are imported lazily here so the module stays import-safe.
    """
    import psycopg2  # noqa: PLC0415 — lazy (import-safe module)

    dsn = _dsn_from_env(cli_dsn)
    if dsn:
        conn = psycopg2.connect(dsn)
    elif os.environ.get("CRM_APP_SECRET_ARN") and os.environ.get("DB_HOST"):
        import boto3  # noqa: PLC0415 — lazy (Secrets Manager only on the ECS path)

        sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        creds = json.loads(
            sm.get_secret_value(SecretId=os.environ["CRM_APP_SECRET_ARN"])["SecretString"])
        conn = psycopg2.connect(
            host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "5432")),
            dbname=os.environ.get("DB_NAME", "uplift"),
            user=creds["username"], password=creds["password"])
    else:
        raise SystemExit(
            "no DB connection configured: set --dsn / UPLIFT_DB_URL, or "
            "CRM_APP_SECRET_ARN + DB_HOST (the ECS one-off path)")
    conn.autocommit = False
    return conn


# --------------------------------------------------------------------------- CLI
def resolve_tenant(cli_tenant: str | None, dataset: dict) -> str:
    """--tenant > TENANT_ID > the fixture's fixed demo uuid."""
    return (cli_tenant or os.environ.get("TENANT_ID")
            or dataset.get("meta", {}).get("tenant_id") or "").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Idempotently load the committed demo-tenant fixture into Postgres as the "
                    "RLS-bound crm_app role (wipe-then-insert; documents embedded via the "
                    "ingest embedder seam).")
    parser.add_argument("--fixture", default=DEFAULT_FIXTURE,
                        help=f"fixture JSON path (default the committed {DEFAULT_FIXTURE})")
    parser.add_argument("--tenant", default=None,
                        help="tenant uuid to load under (default: $TENANT_ID, else fixture meta)")
    parser.add_argument("--dsn", default=None,
                        help="crm_app DSN (default: $UPLIFT_DB_URL, else Secrets Manager via "
                             "$CRM_APP_SECRET_ARN + $DB_HOST)")
    args = parser.parse_args(argv)

    dataset = read_fixture(args.fixture)
    tenant = resolve_tenant(args.tenant, dataset)
    if not tenant:
        parser.error("no tenant id (pass --tenant, set $TENANT_ID, or use a fixture with "
                     "meta.tenant_id)")

    embedder = build_embedder()
    conn = connect_crm_app(args.dsn)
    try:
        counts = load(conn, dataset, tenant_id=tenant, embedder=embedder)
    finally:
        conn.close()

    real = os.environ.get("INGEST_REAL_STORES", "") in ("true", "1")
    sys.stderr.write(
        f"loaded demo tenant {tenant} (embedder={'titan-v2' if real else 'offline-stub'}): "
        f"{counts}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via main() / load() in tests
    raise SystemExit(main())
