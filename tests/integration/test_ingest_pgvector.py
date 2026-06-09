"""Integration: real upsert into `documents` via the pipeline's pgvector store.

Runs only when UPLIFT_TEST_DB_URL (an owner/superuser DSN to load schema.sql +
roles.sql) is set AND a Postgres+pgvector is reachable. Otherwise SKIPS cleanly.

Proves: the full pipeline (fake HubSpot + fake embedder → PgDocumentStore /
PgCursorStore) lands documents, and a second run embeds nothing (incremental).
"""
import os
import urllib.parse as up
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from ingest import EMBEDDING_DIM
from ingest.connectors.hubspot import HUBSPOT_TOKEN_SECRET_REF, HubSpotConnector
from ingest.pipeline import (
    InMemoryRawSink,
    InMemoryStructuredSink,
    PgCursorStore,
    PgDocumentStore,
    sync_tenant,
)

OWNER_URL = os.environ.get("UPLIFT_TEST_DB_URL")
HERE = os.path.dirname(__file__)
DB_DIR = os.path.join(HERE, "..", "..", "db")


class FakeSecrets:
    def get_secret(self, ref):
        return "pat-fake-token"


class FakeHubSpotClient:
    def list_companies(self, since):
        return [] if since else [
            {"id": "co-1", "updatedAt": "2026-01-01T00:00:00Z",
             "properties": {"name": "Acme", "domain": "acme.io"}}
        ]

    def list_contacts(self, since):
        return [] if since else [
            {"id": "ct-1", "updatedAt": "2026-01-02T00:00:00Z",
             "properties": {"firstname": "Ada", "email": "ada@acme.io"}}
        ]

    def list_deals(self, since):
        return [] if since else [
            {"id": "dl-1", "updatedAt": "2026-01-03T00:00:00Z",
             "properties": {"dealname": "Renewal", "amount": "1000"}}
        ]

    def list_notes(self, since):
        return [] if since else [
            {"id": "nt-1", "updatedAt": "2026-01-04T00:00:00Z",
             "properties": {"hs_note_body": "Wants a quote.", "hs_contact_id": "ct-1"}}
        ]


def _fake_embedder(text):
    return [0.0] * EMBEDDING_DIM


def _app_dsn():
    if not OWNER_URL:
        pytest.skip("set UPLIFT_TEST_DB_URL (owner DSN) to run the pgvector upsert test")
    owner = None
    try:
        owner = psycopg2.connect(OWNER_URL)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no reachable Postgres ({e.__class__.__name__})")
    owner.autocommit = True
    with owner.cursor() as cur:
        try:
            cur.execute(open(os.path.join(DB_DIR, "schema.sql")).read())
            cur.execute(open(os.path.join(DB_DIR, "roles.sql")).read())
            cur.execute("ALTER ROLE crm_app PASSWORD 'testpw'")
        except Exception as e:  # noqa: BLE001
            owner.close()
            pytest.skip(f"cannot load schema (needs pgvector + privileges): {e}")
    owner.close()
    parts = up.urlparse(OWNER_URL)
    return up.urlunparse(
        parts._replace(netloc=f"crm_app:testpw@{parts.hostname}:{parts.port or 5432}")
    )


@pytest.mark.integration
def test_pipeline_upserts_documents_and_is_incremental():
    dsn = _app_dsn()
    tenant = str(uuid.uuid4())

    def make_conn():
        return HubSpotConnector(
            tenant, client=FakeHubSpotClient(), secrets=FakeSecrets(),
            raw_sink=InMemoryRawSink(), structured_sink=InMemoryStructuredSink(),
        )

    store = PgDocumentStore(dsn)
    cursors = PgCursorStore(dsn)

    r1 = sync_tenant(tenant, make_conn(), _fake_embedder, store, cursors)
    assert r1.embedded == 4  # company + contact + deal + note
    assert r1.cursor == "2026-01-04T00:00:00Z"

    # Verify rows actually present under this tenant (RLS-scoped).
    with store._conn.cursor() as cur:
        cur.execute("SET app.current_tenant = %s", (tenant,))
        cur.execute("SELECT count(*) FROM documents WHERE tenant_id=%s", (tenant,))
        assert cur.fetchone()[0] == 4

    # Second run: change-filtered connector pulls nothing → embeds nothing.
    r2 = sync_tenant(tenant, make_conn(), _fake_embedder, store, cursors)
    assert r2.pulled == 0
    assert r2.embedded == 0
