"""Unit tests for signup/leads.py — public lead capture.

Covers the MemoryLeadStore (no DB, no psycopg2 required):
  * a valid lead (kind + name + email, with/without optional fields) is inserted and
    the returned id is a UUID string that appears in the row;
  * kind is validated by callers against the allowed set {"book_call", "email"} — the
    store itself is a dumb sink (it does not enforce the enum), but we verify both allowed
    values pass through correctly;
  * missing required fields (kind / name / email passed as empty string or None) do NOT
    produce silently empty rows — the store stores exactly what it received, so the route's
    upstream validation layer owns rejection, and here we confirm the store faithfully
    persists whatever it is handed;
  * the store is APPEND-ONLY — there is no update() or delete() method exposed by either
    store class;
  * optional fields (message, company, source_ip) default to None when omitted and are
    stored as None — no coercion to empty string;
  * field normalization: name/email/message/company are stored verbatim (the route layer
    strips/lowercases, but the store itself does NOT mutate the value — it stores what it
    receives, so a caller-normalised value round-trips exactly);
  * multiple inserts grow the rows list independently (each row has a distinct id).
"""
from __future__ import annotations

import uuid

import pytest

from signup.leads import MemoryLeadStore

# The closed enum of allowed kinds as declared in api/public_routes.py (Literal["book_call", "email"]).
ALLOWED_KINDS = {"book_call", "email"}


# ------------------------------------------------------------------------------- helpers

def _insert(store: MemoryLeadStore, **kwargs) -> str:
    """Thin wrapper so tests can override individual fields without repeating boilerplate."""
    defaults = {
        "kind": "book_call",
        "name": "Alice Smith",
        "email": "alice@example.com",
    }
    defaults.update(kwargs)
    return store.insert(**defaults)


# ------------------------------------------------------------------------------- valid inserts

@pytest.mark.unit
def test_valid_lead_book_call_persists_and_returns_uuid():
    store = MemoryLeadStore()
    lead_id = _insert(store, kind="book_call", name="Alice Smith", email="alice@example.com")

    assert len(store.rows) == 1
    row = store.rows[0]
    assert row["id"] == lead_id
    # Verify the returned string is a valid UUID.
    parsed = uuid.UUID(lead_id)
    assert str(parsed) == lead_id


@pytest.mark.unit
def test_valid_lead_email_kind_persists():
    store = MemoryLeadStore()
    lead_id = _insert(store, kind="email", name="Bob Jones", email="bob@example.com")

    assert len(store.rows) == 1
    row = store.rows[0]
    assert row["kind"] == "email"
    assert row["id"] == lead_id


@pytest.mark.unit
def test_all_allowed_kinds_accepted():
    """Both members of the allowed-kinds set ("book_call" and "email") round-trip correctly."""
    for kind in sorted(ALLOWED_KINDS):
        store = MemoryLeadStore()
        lead_id = _insert(store, kind=kind)
        assert store.rows[0]["kind"] == kind
        assert store.rows[0]["id"] == lead_id


@pytest.mark.unit
def test_optional_fields_message_and_company_stored():
    store = MemoryLeadStore()
    _insert(
        store,
        kind="book_call",
        name="Carol White",
        email="carol@example.com",
        message="Interested in enterprise plan",
        company="ACME Corp",
    )
    row = store.rows[0]
    assert row["message"] == "Interested in enterprise plan"
    assert row["company"] == "ACME Corp"


@pytest.mark.unit
def test_source_ip_stored_when_provided():
    store = MemoryLeadStore()
    _insert(store, source_ip="203.0.113.42")
    assert store.rows[0]["source_ip"] == "203.0.113.42"


# ------------------------------------------------------------------------------- optional fields default to None

@pytest.mark.unit
def test_optional_fields_default_to_none_when_omitted():
    """message, company, and source_ip all default to None — not empty string."""
    store = MemoryLeadStore()
    _insert(store)  # only kind/name/email supplied

    row = store.rows[0]
    assert row["message"] is None
    assert row["company"] is None
    assert row["source_ip"] is None


@pytest.mark.unit
def test_none_message_and_company_stored_as_none():
    """Explicitly passing None for optional fields stores None — no coercion."""
    store = MemoryLeadStore()
    _insert(store, message=None, company=None, source_ip=None)

    row = store.rows[0]
    assert row["message"] is None
    assert row["company"] is None
    assert row["source_ip"] is None


# ------------------------------------------------------------------------------- field normalization (store is a dumb sink)

@pytest.mark.unit
def test_store_preserves_values_verbatim_no_mutation():
    """The store does NOT strip, lowercase, or otherwise normalise values.

    The route layer (public_routes.py) owns normalisation; the store records exactly
    what it receives.  Passing a mixed-case email or padded name must round-trip unchanged.
    """
    store = MemoryLeadStore()
    _insert(
        store,
        kind="book_call",
        name="  David Lee  ",       # leading/trailing spaces — stored as-is
        email="DAVID@Example.COM",  # uppercase — stored as-is
        message="  Hello  ",
        company="  TechCo  ",
    )
    row = store.rows[0]
    assert row["name"] == "  David Lee  "
    assert row["email"] == "DAVID@Example.COM"
    assert row["message"] == "  Hello  "
    assert row["company"] == "  TechCo  "


@pytest.mark.unit
def test_already_normalised_values_round_trip_exactly():
    """Caller-normalised values (stripped/lowercased, as the route does) round-trip exactly."""
    store = MemoryLeadStore()
    _insert(
        store,
        name="Eve Brown",
        email="eve@example.com",
        message="normalised message",
        company="StartupCo",
    )
    row = store.rows[0]
    assert row["name"] == "Eve Brown"
    assert row["email"] == "eve@example.com"
    assert row["message"] == "normalised message"
    assert row["company"] == "StartupCo"


# ------------------------------------------------------------------------------- append-only contract

@pytest.mark.unit
def test_store_is_append_only_no_update_method():
    """MemoryLeadStore must NOT expose an update() or delete() method — leads are immutable once
    written (append-only audit trail, matching the DB REVOKE DELETE grant)."""
    store = MemoryLeadStore()
    assert not hasattr(store, "update"), "leads store must not expose update()"
    assert not hasattr(store, "delete"), "leads store must not expose delete()"


@pytest.mark.unit
def test_multiple_inserts_each_have_distinct_ids():
    """Every insert produces a unique row with a distinct UUID."""
    store = MemoryLeadStore()
    ids = [_insert(store, email=f"user{i}@example.com") for i in range(5)]

    assert len(store.rows) == 5
    assert len(set(ids)) == 5, "every lead must get a unique id"
    for i, row in enumerate(store.rows):
        assert row["id"] == ids[i]


@pytest.mark.unit
def test_prior_rows_immutable_after_second_insert():
    """Inserting a second lead does not mutate the first row."""
    store = MemoryLeadStore()
    id1 = _insert(store, name="First", email="first@example.com", kind="book_call")
    id2 = _insert(store, name="Second", email="second@example.com", kind="email")

    assert store.rows[0]["id"] == id1
    assert store.rows[0]["name"] == "First"
    assert store.rows[1]["id"] == id2
    assert store.rows[1]["name"] == "Second"


# ------------------------------------------------------------------------------- invalid / edge inputs (store stores them; route rejects upstream)

@pytest.mark.unit
def test_unknown_kind_stored_verbatim():
    """The store itself does not enforce the allowed-kinds enum — it is a dumb persistence sink.
    Upstream validation (Pydantic in public_routes.py) rejects bad kinds before the store is
    called; here we verify the store does not introduce a second enforcement layer that would
    mask bugs in the route.
    """
    store = MemoryLeadStore()
    lead_id = _insert(store, kind="unknown_kind")
    assert store.rows[0]["kind"] == "unknown_kind"
    assert store.rows[0]["id"] == lead_id


@pytest.mark.unit
def test_empty_strings_stored_as_is():
    """The store persists empty-string values exactly — the route layer owns the non-empty check."""
    store = MemoryLeadStore()
    lead_id = _insert(store, kind="", name="", email="")
    row = store.rows[0]
    assert row["kind"] == ""
    assert row["name"] == ""
    assert row["email"] == ""
    assert row["id"] == lead_id


@pytest.mark.unit
def test_row_schema_contains_all_expected_keys():
    """Each row dict has exactly the expected keys — no extra or missing fields."""
    store = MemoryLeadStore()
    _insert(store, message="hi", company="Co", source_ip="1.2.3.4")
    row = store.rows[0]
    expected_keys = {"id", "kind", "name", "email", "message", "company", "source_ip"}
    assert set(row.keys()) == expected_keys
