"""Unit: the COMMITTED demo-tenant fixture (scripts/demo/fixture/demo_tenant.json).

The generator (scripts/generate_demo_dataset.py) is exercised by test_demo_dataset_generator.py;
this file guards the *committed artifact* the loader reads and the invariants the loader relies
on — without a database:

  * in sync — the committed JSON is byte-identical to a fresh default generator run (so the
    fixture can never silently drift from the generator)
  * schema-validatable — every saved_view spec_json passes the view-spec schema
  * loader contract — the row sections + keys the loader inserts all exist; documents carry the
    demo:doc: namespace (disjoint from the demo:kb: knowledge corpus the CRM-doc wipe must spare)
  * fabrication discipline — .example domains + 555-01XX phones only, in the committed bytes

The loader module itself imports clean with no AWS/DB (import-safety).
"""
import importlib.util
import json
import os
import re

import pytest

from scripts import generate_demo_dataset as gen
from shared import view_spec

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIXTURE_PATH = os.path.join(ROOT, "scripts", "demo", "fixture", "demo_tenant.json")
LOADER_PATH = os.path.join(ROOT, "scripts", "demo", "load_demo_tenant.py")


@pytest.fixture(scope="module")
def fixture() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _load_loader():
    spec = importlib.util.spec_from_file_location("load_demo_tenant", LOADER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- in sync
@pytest.mark.unit
def test_committed_fixture_matches_generator_default():
    """The committed fixture is exactly `generate_demo_dataset.py` default output — regenerate
    with `python scripts/generate_demo_dataset.py --out scripts/demo/fixture/demo_tenant.json`
    if this fails (the generator changed; the fixture must be re-emitted in the same commit)."""
    expected = gen.to_json(gen.generate())  # default seed 47 + fixed anchor → byte-stable
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        committed = f.read()
    assert committed == expected


@pytest.mark.unit
def test_meta_counts_mirror_sections(fixture):
    counts = fixture["meta"]["counts"]
    for key in ("companies", "contacts", "deals", "activities", "approvals",
                "saved_views", "documents"):
        assert counts[key] == len(fixture[key])
    # The ratified Option B scale (the generator's source of truth).
    assert counts["companies"] == 40
    assert counts["contacts"] == 120
    assert counts["deals"] == 60


# --------------------------------------------------------------------------- loader contract
@pytest.mark.unit
def test_loader_reads_committed_fixture_by_default():
    loader = _load_loader()
    assert loader.DEFAULT_FIXTURE == FIXTURE_PATH
    ds = loader.read_fixture()  # no arg → the committed file
    assert ds["meta"]["counts"]["companies"] == 40
    # resolve_tenant falls back to the fixture's fixed demo uuid when nothing is passed.
    assert loader.resolve_tenant(None, ds) == ds["meta"]["tenant_id"]


@pytest.mark.unit
def test_loader_document_wipe_scope_spares_knowledge_corpus():
    """The loader wipes only its own documents (demo:doc:%), never the demo:kb:% knowledge
    corpus seeded separately — so the two seeders compose in any order."""
    loader = _load_loader()
    assert loader.FIXTURE_DOC_REF_PREFIX == "demo:doc:"
    assert not "demo:kb:".startswith(loader.FIXTURE_DOC_REF_PREFIX)


@pytest.mark.unit
def test_fixture_rows_carry_keys_the_loader_inserts(fixture):
    """Every section the loader INSERTs is present with the id/ref columns it reads."""
    assert {c for c in ("id", "name") if all(c in r for r in fixture["companies"])} == {"id", "name"}
    for r in fixture["deals"]:
        assert {"id", "company_id", "contact_id", "title", "stage", "amount"} <= set(r)
    for r in fixture["approvals"]:
        assert {"id", "proposed_action", "status", "value_at_stake"} <= set(r)
    for r in fixture["saved_views"]:
        assert {"id", "view_id", "spec_json", "semantic_refs"} <= set(r)
    for r in fixture["documents"]:
        assert {"source", "ref_id", "content"} <= set(r)


@pytest.mark.unit
def test_documents_use_demo_doc_namespace(fixture):
    for d in fixture["documents"]:
        assert d["ref_id"].startswith("demo:doc:"), d["ref_id"]
    # unique on (source, ref_id) — the documents UNIQUE (tenant_id, source, ref_id) index
    keys = {(d["source"], d["ref_id"]) for d in fixture["documents"]}
    assert len(keys) == len(fixture["documents"])


# --------------------------------------------------------------------------- saved views valid
@pytest.mark.unit
def test_saved_view_specs_validate_against_schema(fixture):
    ids = set()
    for sv in fixture["saved_views"]:
        view_spec.validate_schema(sv["spec_json"])  # raises on violation
        assert sv["semantic_refs"] == sv["spec_json"]["semantic_refs"]
        ids.add(sv["view_id"])
    assert ids == {"pipeline-health", "renewals-next-90d"}


# --------------------------------------------------------------------------- fabrication
@pytest.mark.unit
def test_committed_bytes_are_undeliverable_by_construction(fixture):
    for c in fixture["contacts"]:
        assert re.fullmatch(r"[a-z0-9]+\.[a-z0-9]+@[a-z0-9]+\.example", c["email"]), c["email"]
        assert re.fullmatch(r"\+1-(512|737|210|830|254|361)-555-01\d{2}", c["phone"]), c["phone"]
    for co in fixture["companies"]:
        assert co["domain"].endswith(".example"), co["domain"]


@pytest.mark.unit
def test_committed_bytes_have_no_real_pii():
    blob = open(FIXTURE_PATH, encoding="utf-8").read()
    # no real-looking email TLDs / freemail providers, and no project principals
    assert not re.search(
        r"@[A-Za-z0-9.-]+\.(com|io|net|org|us|co|ai|edu|gov)\b|"
        r"\b(gmail|yahoo|hotmail|outlook)\b", blob, re.I)
    for name in ("Nick Friesen", "Matthew Yee", "Matt Yee"):
        assert name not in blob
