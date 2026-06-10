"""Unit: the synthetic demo-tenant dataset generator (scripts/generate_demo_dataset.py).

Gates, per the decision brief (docs/decisions/demo-tenant-synthetic-dataset.md, PENDING
ratification — issue #123):
  * determinism — same seed => byte-identical JSON and SQL; different seed => different output
  * shape — 40 companies / 120 contacts / 60 deals on the exact brief funnel; activity bands;
    6 dial-designed pending + 8 decided approvals; 2 saved views; documents = activities + docs
  * schema match — every emitted row key is a real column in db/schema.sql (parsed, not pinned)
  * fabrication discipline — RFC 2606 `.example` domains only, NANP 555-01XX phones with zero
    reuse, no real-PII patterns anywhere in the serialized output
  * approvals reference existing deals; saved views validate against the view-spec schema AND
    the repo Cube member catalog (semantic/model/cubes/*.js)
  * the generator stays stdlib-only and never opens a DB connection
"""
import ast
import os
import re

import pglast
import pytest

from scripts import generate_demo_dataset as gen
from shared import view_spec

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SCHEMA_SQL = os.path.join(ROOT, "db", "schema.sql")
CUBES_DIR = os.path.join(ROOT, "semantic", "model", "cubes")
GENERATOR_SRC = os.path.join(ROOT, "scripts", "generate_demo_dataset.py")


@pytest.fixture(scope="module")
def dataset():
    return gen.generate()


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_same_seed_identical_output():
    a, b = gen.generate(seed=47), gen.generate(seed=47)
    assert gen.to_json(a) == gen.to_json(b)
    assert gen.to_sql(a) == gen.to_sql(b)


@pytest.mark.unit
def test_different_seed_different_output():
    assert gen.to_json(gen.generate(seed=47)) != gen.to_json(gen.generate(seed=48))


@pytest.mark.unit
def test_anchor_is_fixed_not_now():
    """Timestamps derive from the fixed anchor (byte-identical run-to-run), never wall clock."""
    src = open(GENERATOR_SRC, encoding="utf-8").read()
    assert ".now(" not in src and "today()" not in src


# ---------------------------------------------------------------------------
# shape counts (the brief's dataset-spec table)
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_entity_counts(dataset):
    assert len(dataset["companies"]) == 40
    assert len(dataset["contacts"]) == 120
    assert len(dataset["deals"]) == 60
    assert len(dataset["saved_views"]) == 2
    assert dataset["meta"]["counts"]["companies"] == 40  # meta mirrors reality


@pytest.mark.unit
def test_deal_funnel(dataset):
    stages = {}
    for d in dataset["deals"]:
        stages[d["stage"]] = stages.get(d["stage"], 0) + 1
    assert stages == {"new": 14, "qualified": 12, "proposal": 11, "negotiation": 8,
                      "closed_won": 9, "closed_lost": 6}


@pytest.mark.unit
def test_open_pipeline_in_band(dataset):
    open_total = sum(d["amount"] for d in dataset["deals"] if d["stage"] in gen.OPEN_STAGES)
    assert 1_400_000 <= open_total <= 2_800_000  # brief targets ~$2.1M


@pytest.mark.unit
def test_activity_bands(dataset):
    assert 350 <= len(dataset["activities"]) <= 560  # brief: ~480
    per_deal: dict[str, int] = {}
    for a in dataset["activities"]:
        per_deal[a["deal_id"]] = per_deal.get(a["deal_id"], 0) + 1
    by_id = {d["id"]: d for d in dataset["deals"]}
    assert set(per_deal) == set(by_id), "every deal has activities, no orphan activities"
    for deal_id, n in per_deal.items():
        lo, hi = gen.ACTIVITY_BAND[by_id[deal_id]["stage"]]
        assert lo <= n <= hi, f"{by_id[deal_id]['title']}: {n} activities outside [{lo},{hi}]"


@pytest.mark.unit
def test_activities_backdated_business_hours(dataset):
    from datetime import datetime
    anchor = datetime.fromisoformat(dataset["meta"]["anchor_date"] + "T23:59:00-05:00")
    for a in dataset["activities"]:
        t = datetime.fromisoformat(a["occurred_at"])
        assert t < anchor, "every activity is explicitly backdated"
        assert 8 <= t.hour <= 17, "US-Central business hours"
        assert (anchor - t).days <= 160, "trailing ~150-day window"


@pytest.mark.unit
def test_documents_mirror_activities_plus_authored(dataset):
    docs = dataset["documents"]
    assert len(docs) == len(dataset["activities"]) + len(gen.LONG_DOCS)
    assert {d["source"] for d in docs} <= {"call", "email", "upload"}  # schema vocabulary
    assert all(d["ref_id"].startswith("demo:doc:") for d in docs)
    # ref_ids unique (documents has a UNIQUE (tenant_id, source, ref_id) index)
    assert len({(d["source"], d["ref_id"]) for d in docs}) == len(docs)
    long_docs = [d for d in docs if d["ref_id"].startswith("demo:doc:long:")]
    assert len(long_docs) == len(gen.LONG_DOCS)
    assert all(len(d["content"]) > 800 for d in long_docs), "authored docs are substantial"


# ---------------------------------------------------------------------------
# approvals — Greenlight-able, dial-designed, referencing real deals
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_approvals_shape(dataset):
    pending = [a for a in dataset["approvals"] if a["status"] == "pending"]
    decided = [a for a in dataset["approvals"] if a["status"] != "pending"]
    assert len(pending) == 6 and len(decided) == 8
    assert sum(1 for a in decided if a["status"] == "approved") == 6
    denied = [a for a in decided if a["status"] == "denied"]
    assert len(denied) == 2
    for a in denied:
        assert a["deny_message"], "denied rows carry the deny reason"
    for a in decided:
        assert a["decided_by"] and a["decided_at"]
    for a in pending:
        assert a["decided_by"] is None and a["decided_at"] is None


@pytest.mark.unit
def test_pending_approvals_exercise_the_autonomy_dial(dataset):
    """The brief's table: values straddle the L2 thresholds ($1,000 / 10%)."""
    pending = [a for a in dataset["approvals"] if a["status"] == "pending"]
    values = sorted(a["value_at_stake"] for a in pending)
    assert values == [850, 1200, 36000, 48000, 132000, 284000]
    assert any(v < 1000 for v in values), "one flips to AUTO at L2 (the dial demo)"
    assert any(1000 < v <= 1500 for v in values), "one sits just over the $1,000 boundary"
    quote = [a for a in pending if a["proposed_action"]["action"] == "issue_quote"]
    assert quote and quote[0]["proposed_action"]["discount"] >= 0.10, \
        "one pending quote trips the discount guard independently of value"
    actions = {a["proposed_action"]["action"] for a in pending}
    assert actions == {"send_email", "update_deal", "issue_quote"}  # all 3 side-effecting tools


@pytest.mark.unit
def test_approvals_reference_existing_deals(dataset):
    deal_refs = {d["ref_id"] for d in dataset["deals"]}
    for a in dataset["approvals"]:
        assert a["proposed_action"]["deal_ref"] in deal_refs
        assert a["reasoning"], "reasoning reads as traceable agent judgment"
    for a in dataset["approvals"]:
        if a["proposed_action"]["action"] == "send_email":
            assert a["proposed_action"]["to"].endswith(".example")


# ---------------------------------------------------------------------------
# schema-column match (parse db/schema.sql — don't drift from the real shapes)
# ---------------------------------------------------------------------------
def _schema_columns(table: str) -> set[str]:
    sql = open(SCHEMA_SQL, encoding="utf-8").read()
    m = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", sql, re.S)
    assert m, f"no CREATE TABLE for {table}"
    cols = set()
    for line in m.group(1).splitlines():
        line = line.strip()
        word = line.split(" ")[0].strip(",")
        if word and re.fullmatch(r"[a-z_]+", word) and word not in ("PRIMARY", "UNIQUE"):
            cols.add(word)
    return cols


@pytest.mark.unit
@pytest.mark.parametrize("key,table", [
    ("companies", "companies"), ("contacts", "contacts"), ("deals", "deals"),
    ("activities", "activities"), ("approvals", "approvals"),
    ("saved_views", "saved_views"), ("documents", "documents"),
])
def test_rows_match_schema_columns(dataset, key, table):
    cols = _schema_columns(table)
    for row in dataset[key]:
        extra = set(row) - cols
        assert not extra, f"{table} row keys not in db/schema.sql: {extra}"


@pytest.mark.unit
def test_generated_sql_parses_and_is_tenant_scoped(dataset):
    sql = gen.to_sql(dataset)
    stmts = pglast.parse_sql(sql)  # real Postgres grammar
    assert len(stmts) > 10
    assert "SET LOCAL app.current_tenant" in sql, "runs under the RLS-bound tenant GUC"
    assert sql.strip().startswith("--") and "BEGIN;" in sql and sql.strip().endswith("COMMIT;")
    assert "DELETE FROM documents WHERE ref_id LIKE 'demo:%';" in sql, \
        "documents wipe is scoped to the synthetic marker"
    for t in ("companies", "contacts", "deals", "activities", "approvals", "saved_views",
              "documents"):
        assert f"INSERT INTO {t} " in sql
    assert not re.search(r"INSERT INTO documents \([^)]*embedding", sql), \
        "no fake embeddings — the load-time embed pass owns that column"


# ---------------------------------------------------------------------------
# fabrication discipline — zero real PII, undeliverable by construction
# ---------------------------------------------------------------------------
REAL_DOMAIN_PATTERN = re.compile(
    r"@[A-Za-z0-9.-]+\.(com|io|net|org|us|co|ai|edu|gov)\b|"
    r"\b(gmail|yahoo|hotmail|outlook|aol|icloud|protonmail)\b", re.I)
# regression guard: the old seed's plausibly-real domains must never reappear
LEGACY_REAL_DOMAINS = ("birchwoodcap.com", "halcyonlogistics.io", "mesaverdehealth.com",
                       "northbeam.us")
REAL_NAME_DENYLIST = ("Nick Friesen", "Matthew Yee", "Matt Yee")  # project principals


@pytest.mark.unit
def test_no_real_pii_patterns(dataset):
    blob = gen.to_json(dataset)
    assert not REAL_DOMAIN_PATTERN.search(blob), REAL_DOMAIN_PATTERN.search(blob)
    for domain in LEGACY_REAL_DOMAINS:
        assert domain not in blob
    for name in REAL_NAME_DENYLIST:
        assert name not in blob


@pytest.mark.unit
def test_emails_undeliverable_and_unique(dataset):
    emails = [c["email"] for c in dataset["contacts"]]
    assert len(set(emails)) == len(emails) == 120
    for e in emails:
        assert re.fullmatch(r"[a-z0-9]+\.[a-z0-9]+@[a-z0-9]+\.example", e), e
    for c in dataset["companies"]:
        assert c["domain"].endswith(".example"), c["domain"]


@pytest.mark.unit
def test_phones_fictitious_block_zero_reuse(dataset):
    phones = [c["phone"] for c in dataset["contacts"]]
    assert len(set(phones)) == len(phones) == 120
    for p in phones:
        assert re.fullmatch(r"\+1-(512|737|210|830|254|361)-555-01\d{2}", p), p


@pytest.mark.unit
def test_every_row_carries_demo_ref_id(dataset):
    for key, prefix in (("companies", "demo:company:"), ("contacts", "demo:contact:"),
                        ("deals", "demo:deal:"), ("documents", "demo:doc:")):
        for row in dataset[key]:
            assert row["ref_id"].startswith(prefix)


# ---------------------------------------------------------------------------
# saved views — schema-valid AND inside the repo Cube member catalog
# ---------------------------------------------------------------------------
def _cube_catalog() -> set[str]:
    members = set()
    for fname in os.listdir(CUBES_DIR):
        src = open(os.path.join(CUBES_DIR, fname), encoding="utf-8").read()
        cube_name = re.search(r"cube\('(\w+)'", src).group(1)
        for section in ("measures", "dimensions"):
            m = re.search(rf"{section}:\s*{{(.*?)\n  }}", src, re.S)
            assert m, f"{fname}: no {section} block"
            for field in re.findall(r"^\s{4}(\w+):", m.group(1), re.M):
                members.add(f"{cube_name}.{field}")
    return members


@pytest.mark.unit
def test_saved_views_validate_against_schema_and_cube_catalog(dataset):
    catalog = _cube_catalog()
    assert "Deals.pipeline_value" in catalog  # sanity: the parse found real members
    view_ids = set()
    for row in dataset["saved_views"]:
        view_spec.validate(row["spec_json"], allowed_members=catalog)  # raises on violation
        assert row["semantic_refs"] == row["spec_json"]["semantic_refs"]
        assert row["source_prompt"] and row["version"] == 1
        view_ids.add(row["view_id"])
    assert view_ids == {"pipeline-health", "renewals-next-90d"}


# ---------------------------------------------------------------------------
# the generator stays a pure file generator — stdlib-only, no DB/network
# ---------------------------------------------------------------------------
ALLOWED_IMPORTS = {"argparse", "json", "random", "sys", "uuid", "datetime", "__future__"}


@pytest.mark.unit
def test_generator_is_stdlib_only_no_db():
    tree = ast.parse(open(GENERATOR_SRC, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= ALLOWED_IMPORTS, f"non-stdlib/forbidden imports: {imported - ALLOWED_IMPORTS}"
    for forbidden in ("boto3", "psycopg2", "sqlalchemy", "requests", "urllib"):
        assert forbidden not in imported
