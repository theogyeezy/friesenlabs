"""Unit: the scale-packs generator (scripts/generate_test_packs.py).

Gates:
  * tiers — smoke is exactly 5/15/8; demo is byte-identical to the ratified foundation
    (generate_demo_dataset.generate()); load is exactly 2000/6000/3000
  * determinism — same tier => byte-identical JSON and SQL, every run
  * schema match — every emitted row key is a real column in db/schema.sql (parsed, not pinned),
    at smoke AND load scale
  * fabrication discipline at scale — `.example` emails (unique), NANP 555-01XX phones with zero
    reuse across 6000 contacts, unique company domains, no real-PII patterns
  * referential integrity — every contact/deal/activity FK resolves; a deal's contact belongs to
    the deal's company
  * saved views still validate against the view-spec schema + the repo Cube member catalog
  * generated SQL parses (real Postgres grammar) and is RLS-tenant-scoped
  * committed fixtures (smoke, demo) match a fresh generation — drift guard
  * load performance — builds + serializes in < 30s and < 50MB (regenerated, never committed)
  * the wrapper never imports a DB/network client
"""
import ast
import os
import re
import time

import pglast
import pytest

from scripts import generate_test_packs as packs
from scripts import generate_demo_dataset as gen
from shared import view_spec

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SCHEMA_SQL = os.path.join(ROOT, "db", "schema.sql")
CUBES_DIR = os.path.join(ROOT, "semantic", "model", "cubes")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
PACKS_SRC = os.path.join(ROOT, "scripts", "generate_test_packs.py")

COMMITTED_TIERS = ("smoke", "demo")
ALL_TABLES = ("companies", "contacts", "deals", "activities", "approvals",
              "saved_views", "documents")


@pytest.fixture(scope="module")
def smoke():
    return packs.build_tier("smoke")


@pytest.fixture(scope="module")
def load():
    return packs.build_tier("load")


# ---------------------------------------------------------------------------
# tier shapes
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_smoke_exact_shape(smoke):
    assert len(smoke["companies"]) == 5
    assert len(smoke["contacts"]) == 15
    assert len(smoke["deals"]) == 8
    assert len(smoke["saved_views"]) == 2
    assert smoke["meta"]["counts"]["contacts"] == 15  # meta mirrors reality
    assert smoke["meta"]["tier"] == "smoke" and smoke["meta"]["test_pack"] is True


@pytest.mark.unit
def test_load_exact_shape(load):
    assert len(load["companies"]) == 2000
    assert len(load["contacts"]) == 6000
    assert len(load["deals"]) == 3000
    # every deal carries activities; documents mirror activities one-for-one in scaled tiers
    assert len(load["documents"]) == len(load["activities"])
    assert load["meta"]["tier"] == "load"


@pytest.mark.unit
def test_demo_tier_is_the_foundation_verbatim():
    """The demo tier must stay byte-identical to the ratified generator — no divergence."""
    assert gen.to_json(packs.build_tier("demo")) == gen.to_json(gen.generate())


@pytest.mark.unit
def test_smoke_funnel_sums_exactly():
    stages = {}
    for d in packs.build_tier("smoke")["deals"]:
        stages[d["stage"]] = stages.get(d["stage"], 0) + 1
    assert sum(stages.values()) == 8
    assert set(stages) <= set(gen.STAGES)


@pytest.mark.unit
def test_load_funnel_sums_exactly(load):
    stages = {}
    for d in load["deals"]:
        stages[d["stage"]] = stages.get(d["stage"], 0) + 1
    assert sum(stages.values()) == 3000
    assert set(stages) == set(gen.STAGES), "all six stages represented at scale"


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize("tier", ["smoke", "demo", "load"])
def test_tier_deterministic(tier):
    a, b = packs.build_tier(tier), packs.build_tier(tier)
    assert gen.to_json(a) == gen.to_json(b)
    assert gen.to_sql(a) == gen.to_sql(b)


@pytest.mark.unit
def test_tiers_have_distinct_tenants():
    tenants = {t: packs.build_tier(t)["meta"]["tenant_id"] for t in ("smoke", "demo", "load")}
    assert len(set(tenants.values())) == 3, f"tenants collide: {tenants}"


# ---------------------------------------------------------------------------
# schema-column match (parse db/schema.sql — same approach as the foundation test)
# ---------------------------------------------------------------------------
def _schema_columns(table: str) -> set[str]:
    sql = open(SCHEMA_SQL, encoding="utf-8").read()
    m = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", sql, re.S)
    assert m, f"no CREATE TABLE for {table}"
    cols = set()
    for line in m.group(1).splitlines():
        word = line.strip().split(" ")[0].strip(",")
        if word and re.fullmatch(r"[a-z_]+", word) and word not in ("PRIMARY", "UNIQUE"):
            cols.add(word)
    return cols


@pytest.mark.unit
@pytest.mark.parametrize("tier", ["smoke", "load"])
@pytest.mark.parametrize("table", ALL_TABLES)
def test_rows_match_schema_columns(tier, table, smoke, load):
    dataset = smoke if tier == "smoke" else load
    cols = _schema_columns(table)
    for row in dataset[table]:
        extra = set(row) - cols
        assert not extra, f"{tier}/{table} row keys not in db/schema.sql: {extra}"


# ---------------------------------------------------------------------------
# referential integrity (every FK resolves; a deal's contact is in the deal's company)
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize("tier", ["smoke", "load"])
def test_referential_integrity(tier, smoke, load):
    d = smoke if tier == "smoke" else load
    company_ids = {c["id"] for c in d["companies"]}
    contact_ids = {c["id"] for c in d["contacts"]}
    deal_ids = {x["id"] for x in d["deals"]}
    company_of_contact = {c["id"]: c["company_id"] for c in d["contacts"]}
    for c in d["contacts"]:
        assert c["company_id"] in company_ids
    for x in d["deals"]:
        assert x["company_id"] in company_ids and x["contact_id"] in contact_ids
        assert company_of_contact[x["contact_id"]] == x["company_id"], \
            "a deal's contact must belong to the deal's company"
    for a in d["activities"]:
        assert a["deal_id"] in deal_ids and a["contact_id"] in contact_ids
    for ap in d["approvals"]:
        assert ap["proposed_action"]["deal_ref"] in {x["ref_id"] for x in d["deals"]}


# ---------------------------------------------------------------------------
# fabrication discipline at scale
# ---------------------------------------------------------------------------
REAL_DOMAIN_PATTERN = re.compile(
    r"@[A-Za-z0-9.-]+\.(com|io|net|org|us|co|ai|edu|gov)\b|"
    r"\b(gmail|yahoo|hotmail|outlook|aol|icloud|protonmail)\b", re.I)


@pytest.mark.unit
@pytest.mark.parametrize("tier", ["smoke", "load"])
def test_emails_undeliverable_and_unique(tier, smoke, load):
    d = smoke if tier == "smoke" else load
    emails = [c["email"] for c in d["contacts"]]
    assert len(set(emails)) == len(emails), "no email reuse"
    for e in emails:
        assert re.fullmatch(r"[a-z0-9]+\.[a-z0-9]+@[a-z0-9.\-]+\.example", e), e
    domains = [c["domain"] for c in d["companies"]]
    assert len(set(domains)) == len(domains), "company domains unique"
    assert all(dom.endswith(".example") for dom in domains)


@pytest.mark.unit
@pytest.mark.parametrize("tier", ["smoke", "load"])
def test_phones_fictitious_block_zero_reuse(tier, smoke, load):
    d = smoke if tier == "smoke" else load
    phones = [c["phone"] for c in d["contacts"]]
    assert len(set(phones)) == len(phones), "555-01XX block exhausted — area-code pool too small"
    for p in phones:
        assert re.fullmatch(r"\+1-\d{3}-555-01\d{2}", p), p


@pytest.mark.unit
@pytest.mark.parametrize("tier", ["smoke", "load"])
def test_every_row_carries_demo_ref_id(tier, smoke, load):
    d = smoke if tier == "smoke" else load
    for key, prefix in (("companies", "demo:company:"), ("contacts", "demo:contact:"),
                        ("deals", "demo:deal:"), ("documents", "demo:doc:")):
        for row in d[key]:
            assert row["ref_id"].startswith(prefix)


@pytest.mark.unit
def test_smoke_no_real_pii_patterns(smoke):
    blob = gen.to_json(smoke)
    assert not REAL_DOMAIN_PATTERN.search(blob), REAL_DOMAIN_PATTERN.search(blob)
    for name in ("Nick Friesen", "Matthew Yee", "Matt Yee"):
        assert name not in blob


# ---------------------------------------------------------------------------
# SQL parses and is RLS-tenant-scoped
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_smoke_sql_parses_and_is_tenant_scoped(smoke):
    sql = gen.to_sql(smoke)
    assert len(pglast.parse_sql(sql)) > 10  # real Postgres grammar
    assert "SET LOCAL app.current_tenant" in sql
    assert sql.strip().startswith("--") and "BEGIN;" in sql and sql.strip().endswith("COMMIT;")
    assert "DELETE FROM documents WHERE ref_id LIKE 'demo:%';" in sql
    for t in ALL_TABLES:
        assert f"INSERT INTO {t} " in sql
    assert not re.search(r"INSERT INTO documents \([^)]*embedding", sql), \
        "no fake embeddings — the load-time embed pass owns that column"


@pytest.mark.unit
def test_load_sql_is_well_formed_and_scoped(load):
    """The scaled SQL path runs at volume. Grammar is identical to smoke (already pglast-parsed),
    so this asserts structure cheaply rather than re-parsing ~16MB of SQL in the unit suite."""
    sql = gen.to_sql(load)
    assert sql.strip().startswith("--") and "BEGIN;" in sql and sql.strip().endswith("COMMIT;")
    assert "SET LOCAL app.current_tenant" in sql
    for t in ALL_TABLES:
        assert f"INSERT INTO {t} " in sql
    assert load["meta"]["tenant_id"] in sql, "SQL is scoped to the load tier's distinct tenant"


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
def test_smoke_saved_views_validate(smoke):
    catalog = _cube_catalog()
    assert "Deals.pipeline_value" in catalog  # sanity: the parse found real members
    for row in smoke["saved_views"]:
        view_spec.validate(row["spec_json"], allowed_members=catalog)  # raises on violation
    assert {r["view_id"] for r in smoke["saved_views"]} == {"pipeline-health", "renewals-next-90d"}


# ---------------------------------------------------------------------------
# committed fixtures match a fresh generation (drift guard)
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize("tier", COMMITTED_TIERS)
def test_committed_fixtures_match_generation(tier):
    path = os.path.join(FIXTURES, tier, "dataset.json")
    assert os.path.exists(path), f"committed fixture missing: {path} (run generate_test_packs.py)"
    with open(path, encoding="utf-8") as f:
        committed = f.read()
    assert committed == gen.to_json(packs.build_tier(tier)), \
        f"{tier} fixture drifted — regenerate with `python scripts/generate_test_packs.py --tier {tier}`"


@pytest.mark.unit
def test_load_tier_is_not_committed():
    """The load pack is regenerated, never stored (it would be ~19MB)."""
    load_dir = os.path.join(FIXTURES, "load")
    assert os.path.exists(os.path.join(load_dir, "README.md"))
    gitignore = open(os.path.join(load_dir, ".gitignore"), encoding="utf-8").read()
    assert "dataset.json" in gitignore and "dataset.sql" in gitignore


# ---------------------------------------------------------------------------
# load performance budget (regenerate into a temp dir; assert time + size)
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_load_within_time_and_size_budget(tmp_path):
    start = time.perf_counter()
    dataset = packs.build_tier("load")
    text = gen.to_json(dataset)
    elapsed = time.perf_counter() - start
    size_mb = len(text.encode("utf-8")) / (1024 * 1024)
    assert elapsed < 30, f"load generation took {elapsed:.1f}s (budget 30s)"
    assert size_mb < 50, f"load JSON is {size_mb:.1f}MB (budget 50MB)"


# ---------------------------------------------------------------------------
# the wrapper never reaches for a DB/network client
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_wrapper_imports_no_db_or_network():
    tree = ast.parse(open(PACKS_SRC, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    for forbidden in ("boto3", "psycopg2", "psycopg", "sqlalchemy", "requests", "urllib3"):
        assert forbidden not in imported, f"wrapper must not import {forbidden}"
