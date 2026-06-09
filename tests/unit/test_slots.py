"""Unit: slot resolution (Build Guide Step 36).

Date phrases resolve to the right ranges with an injected `today`. A name resolves to an id via a
fake tenant-scoped CRM. Multiple matches return a Disambiguation (no silent guess). An unknown
reference resolves to nothing.
"""
from datetime import date

import pytest

from conv.slots import Disambiguation, SlotContext, resolve_date_range, resolve_slots


# --------------------------------------------------------------------------- fakes
class FakeCrm:
    """Tenant-scoped CRM lookup fake. Only returns rows for the matching tenant."""

    def __init__(self, companies, contacts):
        # {tenant_id: {name_lower: [rows]}}
        self.companies = companies
        self.contacts = contacts
        self.seen_tenants = []

    def find_companies(self, tenant_id, name):
        self.seen_tenants.append(tenant_id)
        return self.companies.get(tenant_id, {}).get(name.lower(), [])

    def find_contacts(self, tenant_id, name):
        self.seen_tenants.append(tenant_id)
        return self.contacts.get(tenant_id, {}).get(name.lower(), [])


class FakeCube:
    def __init__(self, values):
        self.values = values  # {dimension: [values]}

    def dimension_values(self, tenant_id, dimension):
        return self.values.get(dimension, [])


class ConfidentDisambiguator:
    def __init__(self, index, confidence):
        self._index = index
        self._confidence = confidence
        self.calls = []

    def pick(self, *, text, slot, candidates):
        self.calls.append((slot, len(candidates)))
        return {"index": self._index, "confidence": self._confidence}


TODAY = date(2026, 5, 15)  # Q2; injected so date math is deterministic


def _ctx(**kw):
    return SlotContext(tenant_id=kw.pop("tenant_id", "tenant-A"), today=kw.pop("today", TODAY), **kw)


# --------------------------------------------------------------------------- date phrases
@pytest.mark.unit
@pytest.mark.parametrize(
    "phrase,start,end",
    [
        ("revenue last quarter", "2026-01-01", "2026-03-31"),
        ("show this quarter", "2026-04-01", "2026-06-30"),
        ("deals this month", "2026-05-01", "2026-05-31"),
        ("pipeline last month", "2026-04-01", "2026-04-30"),
        ("results this year", "2026-01-01", "2026-12-31"),
        ("compare last year", "2025-01-01", "2025-12-31"),
        ("numbers year-to-date", "2026-01-01", "2026-05-15"),
    ],
)
def test_date_phrases_resolve_with_injected_today(phrase, start, end):
    dr = resolve_date_range(phrase, TODAY)
    assert dr is not None
    assert (dr["start"], dr["end"]) == (start, end)


@pytest.mark.unit
def test_last_quarter_in_q1_rolls_to_prior_year():
    # In January, "last quarter" is Q4 of the previous year.
    dr = resolve_date_range("last quarter", date(2026, 1, 10))
    assert (dr["start"], dr["end"]) == ("2025-10-01", "2025-12-31")


@pytest.mark.unit
def test_no_date_phrase_returns_none():
    assert resolve_date_range("how are my biggest deals doing", TODAY) is None


@pytest.mark.unit
def test_resolve_slots_includes_date_range():
    out = resolve_slots("revenue last quarter for the team", _ctx())
    assert out.slots["date_range"]["start"] == "2026-01-01"


# --------------------------------------------------------------------------- company / contact -> id
@pytest.mark.unit
def test_company_name_resolves_to_company_id():
    crm = FakeCrm(
        companies={"tenant-A": {"acme": [{"id": "co-1", "name": "Acme", "domain": "acme.com"}]}},
        contacts={},
    )
    out = resolve_slots("how is the Acme account doing", _ctx(crm=crm))
    assert out.slots["company_id"] == "co-1"
    assert out.ambiguous == []
    assert crm.seen_tenants == ["tenant-A"]  # tenant-scoped lookup


@pytest.mark.unit
def test_contact_name_resolves_to_contact_id():
    crm = FakeCrm(
        companies={},
        contacts={"tenant-A": {"jane doe": [{"id": "ct-9", "name": "Jane Doe"}]}},
    )
    out = resolve_slots("email Jane Doe about the renewal", _ctx(crm=crm))
    assert out.slots["contact_id"] == "ct-9"


# --------------------------------------------------------------------------- ambiguity (never guess)
@pytest.mark.unit
def test_multiple_company_matches_returns_disambiguation_not_a_guess():
    crm = FakeCrm(
        companies={
            "tenant-A": {
                "acme": [
                    {"id": "co-1", "name": "Acme Corp", "domain": "acme.com"},
                    {"id": "co-2", "name": "Acme Industries", "domain": "acme-ind.com"},
                ]
            }
        },
        contacts={},
    )
    out = resolve_slots("the Acme account", _ctx(crm=crm))
    # No silent guess: company_id is NOT in slots; an ambiguity is surfaced instead.
    assert "company_id" not in out.slots
    assert out.needs_disambiguation
    dis = out.ambiguous[0]
    assert isinstance(dis, Disambiguation)
    assert dis.slot == "company_id"
    assert {c.value for c in dis.candidates} == {"co-1", "co-2"}
    assert dis.prompt  # a human-facing prompt is provided


@pytest.mark.unit
def test_confident_disambiguator_may_pick_high_confidence():
    crm = FakeCrm(
        companies={
            "tenant-A": {
                "acme": [
                    {"id": "co-1", "name": "Acme Corp"},
                    {"id": "co-2", "name": "Acme Industries"},
                ]
            }
        },
        contacts={},
    )
    dz = ConfidentDisambiguator(index=1, confidence=0.99)
    out = resolve_slots("the Acme account", _ctx(crm=crm, disambiguator=dz))
    assert out.slots["company_id"] == "co-2"
    assert out.ambiguous == []


@pytest.mark.unit
def test_low_confidence_disambiguator_does_not_get_to_pick():
    crm = FakeCrm(
        companies={
            "tenant-A": {
                "acme": [{"id": "co-1", "name": "Acme Corp"}, {"id": "co-2", "name": "Acme Industries"}]
            }
        },
        contacts={},
    )
    dz = ConfidentDisambiguator(index=1, confidence=0.40)  # below threshold
    out = resolve_slots("the Acme account", _ctx(crm=crm, disambiguator=dz))
    assert "company_id" not in out.slots
    assert out.needs_disambiguation


# --------------------------------------------------------------------------- unknown refs
@pytest.mark.unit
def test_unknown_company_is_unresolved_not_invented():
    crm = FakeCrm(companies={"tenant-A": {}}, contacts={})
    out = resolve_slots("the Globex account", _ctx(crm=crm))
    assert "company_id" not in out.slots
    assert "company_id" in out.unresolved
    assert out.ambiguous == []


# --------------------------------------------------------------------------- cube dimension value
@pytest.mark.unit
def test_dimension_value_resolves_to_cube_dimension():
    cube = FakeCube({"region": ["Riverside", "Lakeside", "Downtown"]})
    out = resolve_slots("how is Riverside performing", _ctx(cube=cube))
    assert out.slots["dimension"] == {"dimension": "region", "value": "Riverside"}


@pytest.mark.unit
def test_ambiguous_dimension_values_return_disambiguation():
    cube = FakeCube({"region": ["Riverside", "Lakeside"]})
    out = resolve_slots("compare Riverside and Lakeside", _ctx(cube=cube))
    assert "dimension" not in out.slots
    assert any(d.slot == "dimension" for d in out.ambiguous)
