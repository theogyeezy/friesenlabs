"""Integration: POST /integrations/csv/import — claims-bound multipart CSV import.

Proves:
  * 401 unauth (the shared current_tenant dependency)
  * honest 503 when the importer is unconfigured — never a fake "imported"
  * tenant ALWAYS from the verified claims — a smuggled form tenant is ignored
  * the 5MB cap answers 413 BEFORE the importer runs
  * entity/mapping validation answers 422 (bad entity, non-JSON / non-object
    mapping) before any bytes are read
  * whole-file CsvImportError surfaces as 422 with the actionable message;
    unexpected importer failures surface as a generic 502
  * end-to-end through the REAL ingest path (import_csv + in-memory stores):
    rows land under the claims tenant, per-row errors come back in the report,
    and re-importing the same file is idempotent
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.integrations_routes import MAX_CSV_IMPORT_BYTES, IntegrationsDeps
from api.views import SavedViews
from ingest import EMBEDDING_DIM
from ingest.connectors.csv_import import import_csv
from ingest.pipeline import (
    InMemoryCursorStore,
    InMemoryDocumentStore,
    InMemoryRawSink,
    InMemoryStructuredSink,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "connectors" / "csv"
H = {"Authorization": "Bearer t"}
CSV_BYTES = b"Email,Name\nava@acme.test,Ava Martinez\nbad-email,Nope\n"


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}


def _client(integrations=None):
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
        integrations=integrations if integrations is not None else IntegrationsDeps(),
    )
    return TestClient(create_app(deps))


def _post(client, *, data: bytes = CSV_BYTES, entity: str = "contacts",
          mapping: str | None = None, extra_form: dict | None = None):
    form = {"entity": entity}
    if mapping is not None:
        form["mapping"] = mapping
    if extra_form:
        form.update(extra_form)
    return client.post(
        "/integrations/csv/import",
        files={"file": ("contacts.csv", data, "text/csv")},
        data=form,
        headers=H,
    )


class RecordingImporter:
    """Fake importer — records the call, returns a fixed report."""

    def __init__(self):
        self.calls = []

    def __call__(self, tenant_id, entity, data, mapping):
        self.calls.append((tenant_id, entity, bytes(data), mapping))
        return {"entity": entity, "total_rows": 2, "imported": 1,
                "errors": [{"row": 3, "error": "invalid email 'bad-email'"}]}


# --------------------------------------------------------------------------- auth + honesty
@pytest.mark.integration
def test_unauth_401():
    client = _client(IntegrationsDeps(csv_importer=RecordingImporter()))
    r = client.post("/integrations/csv/import",
                    files={"file": ("x.csv", CSV_BYTES, "text/csv")},
                    data={"entity": "contacts"})
    assert r.status_code == 401


@pytest.mark.integration
def test_unconfigured_503_never_fake_success():
    r = _post(_client())  # default IntegrationsDeps: importer is None
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]
    assert "imported" not in r.text


# --------------------------------------------------------------------------- claims binding
@pytest.mark.integration
def test_importer_called_with_claims_tenant_smuggled_form_tenant_ignored():
    importer = RecordingImporter()
    client = _client(IntegrationsDeps(csv_importer=importer))
    r = _post(client, extra_form={"tenant_id": "B", "tenant": "B"})
    assert r.status_code == 200
    assert len(importer.calls) == 1
    tenant_id, entity, data, mapping = importer.calls[0]
    assert tenant_id == "A"          # the VERIFIED claim, never the form
    assert entity == "contacts"
    assert data == CSV_BYTES
    assert mapping is None
    report = r.json()["report"]
    assert report["imported"] == 1
    assert report["errors"] == [{"row": 3, "error": "invalid email 'bad-email'"}]


@pytest.mark.integration
def test_mapping_form_field_parsed_and_threaded():
    importer = RecordingImporter()
    client = _client(IntegrationsDeps(csv_importer=importer))
    r = _post(client, mapping='{"email": "Email"}')
    assert r.status_code == 200
    assert importer.calls[0][3] == {"email": "Email"}


# --------------------------------------------------------------------------- request validation
@pytest.mark.integration
def test_bad_entity_422_before_importer_runs():
    importer = RecordingImporter()
    client = _client(IntegrationsDeps(csv_importer=importer))
    r = _post(client, entity="leads")
    assert r.status_code == 422
    assert "unknown entity" in r.json()["detail"]
    assert importer.calls == []


@pytest.mark.integration
def test_bad_mapping_422_before_importer_runs():
    importer = RecordingImporter()
    client = _client(IntegrationsDeps(csv_importer=importer))
    assert _post(client, mapping="not json").status_code == 422
    assert _post(client, mapping='["list"]').status_code == 422
    assert _post(client, mapping='{"email": 5}').status_code == 422
    assert importer.calls == []


@pytest.mark.integration
def test_over_5mb_413_before_importer_runs():
    importer = RecordingImporter()
    client = _client(IntegrationsDeps(csv_importer=importer))
    r = _post(client, data=b"a" * (MAX_CSV_IMPORT_BYTES + 1))
    assert r.status_code == 413
    assert importer.calls == []


@pytest.mark.integration
def test_empty_file_422():
    client = _client(IntegrationsDeps(csv_importer=RecordingImporter()))
    assert _post(client, data=b"").status_code == 422


# --------------------------------------------------------------------------- failure surfaces
@pytest.mark.integration
def test_whole_file_problems_surface_as_422_with_message():
    def importer(tenant_id, entity, data, mapping):
        from ingest.connectors.csv_import import CsvImportError
        raise CsvImportError("contacts CSV needs an email column (the natural key)")

    r = _post(_client(IntegrationsDeps(csv_importer=importer)))
    assert r.status_code == 422
    assert "email column" in r.json()["detail"]


@pytest.mark.integration
def test_unexpected_importer_failure_502_generic():
    def importer(tenant_id, entity, data, mapping):
        raise RuntimeError("db exploded with secrets in the message")

    r = _post(_client(IntegrationsDeps(csv_importer=importer)))
    assert r.status_code == 502
    assert "db exploded" not in r.text


# --------------------------------------------------------------------------- end-to-end
@pytest.mark.integration
def test_end_to_end_through_the_real_ingest_path():
    """The route wired to the REAL import_csv over in-memory stores: messy
    fixture in -> per-row error report out, rows landed under the claims
    tenant, second upload idempotent."""
    store, cursors = InMemoryDocumentStore(), InMemoryCursorStore()

    def importer(tenant_id, entity, data, mapping):
        return import_csv(
            tenant_id, entity, data, mapping,
            store=store, cursor_store=cursors,
            embedder=lambda t: [0.5] * EMBEDDING_DIM,
            raw_sink=InMemoryRawSink(), structured_sink=InMemoryStructuredSink(),
        ).to_dict()

    client = _client(IntegrationsDeps(csv_importer=importer))
    messy = (FIXTURES / "contacts_messy.csv").read_bytes()

    r = _post(client, data=messy)
    assert r.status_code == 200
    report = r.json()["report"]
    assert report["total_rows"] == 6
    assert report["imported"] == 3
    assert {e["row"] for e in report["errors"]} == {5, 6, 7}
    # landed under tenant A (the verified claim), natural-key refs
    assert ("A", "csv", "csv-contact:ava@acme.test") in store.docs

    again = _post(client, data=messy).json()["report"]
    assert again["embedded"] == 0
    assert again["skipped_unchanged"] == 3
    assert len(store.docs) == 3  # idempotent — no duplicates
