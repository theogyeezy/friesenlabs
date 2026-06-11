"""Unit: CSV import (ingest/connectors/csv_import.py) — parse / map / validate /
land through the EXISTING ingest path.

All offline (in-memory stores, stub embedder). Proves, against deliberately
MESSY fixtures (tests/fixtures/connectors/csv/):
  * BOM-prefixed UTF-8 + mixed-case/punctuated headers parse + map (heuristics)
  * semicolon-delimited exports are sniffed correctly
  * per-row errors (bad email, missing natural key, unparseable amount) are
    REPORTED with real file line numbers (blank lines counted) — never fatal
  * in-file duplicates keep the first row and report the later one
  * explicit mapping param overrides the header heuristics (and is validated)
  * deterministic natural-key ref_ids -> re-importing the same file is
    idempotent (zero new embeddings, everything skipped-unchanged)
  * whole-file problems (size cap, empty, no natural-key column, bad entity)
    raise CsvImportError
  * CsvConnector enforces the no-cross-tenant guard and the auth-before-pull rule
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ingest import EMBEDDING_DIM
from ingest.connectors.csv_import import (
    CSV_ENTITIES,
    CsvConnector,
    CsvImportError,
    MAX_CSV_BYTES,
    detect_mapping,
    import_csv,
    parse_csv,
    rows_to_records,
)
from ingest.connectors.base import NormalizedRecord
from ingest.pipeline import (
    InMemoryCursorStore,
    InMemoryDocumentStore,
    InMemoryRawSink,
    InMemoryStructuredSink,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "connectors" / "csv"
TENANT = "11111111-1111-1111-1111-111111111111"


def _embed(text: str) -> list[float]:
    return [0.5] * EMBEDDING_DIM


def _run(entity: str, data: bytes, mapping=None, *, store=None, cursors=None):
    store = store if store is not None else InMemoryDocumentStore()
    cursors = cursors if cursors is not None else InMemoryCursorStore()
    report = import_csv(
        TENANT, entity, data, mapping,
        store=store, cursor_store=cursors, embedder=_embed,
        raw_sink=InMemoryRawSink(), structured_sink=InMemoryStructuredSink(),
    )
    return report, store, cursors


# --------------------------------------------------------------------------- parse
@pytest.mark.unit
def test_parse_strips_bom_and_keeps_real_line_numbers():
    data = (FIXTURES / "contacts_messy.csv").read_bytes()
    assert data[:3] == b"\xef\xbb\xbf"  # the fixture really is BOM-prefixed
    headers, rows, lines = parse_csv(data)
    assert headers[0] == "First Name"  # BOM gone
    # 7 file lines: header + 6 data rows, one of which is blank (skipped but counted)
    assert len(rows) == 6
    assert lines == [2, 3, 5, 6, 7, 8]


@pytest.mark.unit
def test_parse_sniffs_semicolon_delimiter():
    headers, rows, _ = parse_csv((FIXTURES / "companies_semicolon.csv").read_bytes())
    assert headers == ["Company Name", "Website"]
    assert rows[0]["Company Name"] == "Acme Fencing"


@pytest.mark.unit
def test_parse_whole_file_problems():
    with pytest.raises(CsvImportError, match="empty"):
        parse_csv(b"")
    with pytest.raises(CsvImportError, match="empty"):
        parse_csv(b"   \n  \n")
    with pytest.raises(CsvImportError, match="cap"):
        parse_csv(b"a" * (MAX_CSV_BYTES + 1))


# --------------------------------------------------------------------------- mapping
@pytest.mark.unit
def test_header_heuristics_detect_messy_contact_headers():
    headers, _, _ = parse_csv((FIXTURES / "contacts_messy.csv").read_bytes())
    mapping = detect_mapping(headers, "contacts")
    assert mapping == {
        "first_name": "First Name",
        "last_name": "LAST NAME",
        "email": "E-Mail",
        "phone": "Phone Number",
        "company": "Company",
    }


@pytest.mark.unit
def test_explicit_mapping_overrides_and_is_validated():
    headers = ["Customer Mail", "Who"]
    # heuristics alone find no email column -> hard error
    with pytest.raises(CsvImportError, match="email column"):
        detect_mapping(headers, "contacts")
    # explicit mapping fixes it
    mapping = detect_mapping(headers, "contacts",
                             {"email": "Customer Mail", "name": "Who"})
    assert mapping["email"] == "Customer Mail"
    assert mapping["name"] == "Who"
    # explicit mapping naming a column the file lacks is rejected
    with pytest.raises(CsvImportError, match="not found"):
        detect_mapping(headers, "contacts", {"email": "No Such Column"})
    # ...as is an unknown canonical field
    with pytest.raises(CsvImportError, match="unknown field"):
        detect_mapping(headers, "contacts", {"shoe_size": "Who"})


@pytest.mark.unit
def test_unknown_entity_and_missing_natural_keys():
    with pytest.raises(CsvImportError, match="unknown entity"):
        detect_mapping(["a"], "leads")
    with pytest.raises(CsvImportError, match="name or domain"):
        detect_mapping(["Phone"], "companies")
    with pytest.raises(CsvImportError, match="title"):
        detect_mapping(["Amount"], "deals")
    assert CSV_ENTITIES == ("contacts", "companies", "deals")


# --------------------------------------------------------------------------- rows
@pytest.mark.unit
def test_contacts_messy_per_row_errors_and_dupes():
    report, store, _ = _run("contacts", (FIXTURES / "contacts_messy.csv").read_bytes())
    assert report.total_rows == 6
    assert report.imported == 3  # Ava, Ben, Eve
    by_row = {e["row"]: e["error"] for e in report.errors}
    assert "invalid email" in by_row[5]            # not-an-email
    assert "missing email" in by_row[6]            # Dan has no email
    assert "duplicate of row 2" in by_row[7]       # AVA@acme.test == ava@acme.test
    assert set(by_row) == {5, 6, 7}
    # natural-key ref_ids landed in documents (chunk ref == record ref, 1 chunk each)
    assert (TENANT, "csv", "csv-contact:ava@acme.test") in store.docs
    assert (TENANT, "csv", "csv-contact:eve@stone.test") in store.docs


@pytest.mark.unit
def test_companies_semicolon_domains_normalized():
    report, store, _ = _run("companies", (FIXTURES / "companies_semicolon.csv").read_bytes())
    assert report.imported == 4
    assert report.errors == []
    # URL-ish website squeezed to a bare domain for the natural key
    assert (TENANT, "csv", "csv-company:acme.test") in store.docs
    # name-only company keys off the lowercased name
    assert (TENANT, "csv", "csv-company:hilltop") in store.docs
    # domain-only row is its own key (no collision with the name-only row)
    assert (TENANT, "csv", "csv-company:hilltop-should-not-dupe.test") in store.docs


@pytest.mark.unit
def test_deals_messy_amounts_and_defaults():
    report, store, _ = _run("deals", (FIXTURES / "deals_messy.csv").read_bytes())
    assert report.total_rows == 5
    assert report.imported == 3
    by_row = {e["row"]: e["error"] for e in report.errors}
    assert "missing deal title" in by_row[4]
    assert "unparseable amount" in by_row[5]
    # currency symbols + thousands separators parse; defaults applied
    doc = store.docs[(TENANT, "csv", "csv-deal:acme backyard fence|ava@acme.test")]
    assert "4800.0 USD" in doc["content"]
    blank = store.docs[(TENANT, "csv", "csv-deal:stone patio quote|eve@stone.test")]
    assert "Stage: new" in blank["content"]  # empty stage defaulted


# --------------------------------------------------------------------------- idempotency
@pytest.mark.unit
def test_reimport_same_file_is_idempotent():
    data = (FIXTURES / "contacts_messy.csv").read_bytes()
    first, store, cursors = _run("contacts", data)
    assert first.embedded == first.imported == 3
    second, _, _ = _run("contacts", data, store=store, cursors=cursors)
    assert second.embedded == 0                      # nothing re-embedded
    assert second.skipped_unchanged == 3             # every chunk content-hash matched
    assert len(store.docs) == 3                      # zero duplicates created
    # csv keeps no cursor — every import is a full pass
    assert cursors.get(TENANT, "csv") is None


# --------------------------------------------------------------------------- connector shim
@pytest.mark.unit
def test_csv_connector_rejects_cross_tenant_records():
    rec = NormalizedRecord(tenant_id="other-tenant", source="csv", ref_id="r1",
                           table="contacts", row={"tenant_id": "other-tenant"}, raw={})
    with pytest.raises(ValueError, match="cross-tenant"):
        CsvConnector(TENANT, [rec], raw_sink=InMemoryRawSink(),
                     structured_sink=InMemoryStructuredSink())


@pytest.mark.unit
def test_csv_connector_requires_authenticate_before_pull():
    conn = CsvConnector(TENANT, [], raw_sink=InMemoryRawSink(),
                        structured_sink=InMemoryStructuredSink())
    with pytest.raises(RuntimeError, match="authenticate"):
        list(conn.pull(None))
    conn.authenticate()
    assert list(conn.pull(None)) == []


@pytest.mark.unit
def test_rows_to_records_default_line_numbers():
    # without parse-supplied lines, rows are assumed contiguous after the header
    _, errors = rows_to_records(TENANT, "contacts",
                                [{"Email": "nope"}], {"email": "Email"})
    assert errors == [{"row": 2, "error": "invalid email 'nope'"}]
