"""Unit: the data-plane SQL is grammar-valid and EVERY tenant-scoped table is RLS-FORCE'd.

This is a static gate that runs with no database — it parses the SQL against the real Postgres
grammar (libpg_query via pglast) and asserts the RLS contract, catching the #1 isolation gotcha
("forgot FORCE") automatically.

HONESTY GUARANTEE: the tenant-table list is DERIVED from db/schema.sql itself, never frozen in
this file. A tenant table is any CREATE TABLE whose tenant_id column is mandatory (NOT NULL or
PRIMARY KEY) and that is not explicitly marked `-- RLS-EXEMPT: <reason>` in the comment block
above its CREATE TABLE (the pre-tenant signup tables). The gate cross-checks that derived set
against the schema's own `tenant_tables` DO-block array AND against the crm_app GRANTs in
roles.sql — so adding a tenant table without RLS, without the array entry, or without a GRANT
(the fresh-load zero-privilege gap) fails here instead of silently going stale.
"""
import os
import re

import pglast
import pytest

DB_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "db")
SCHEMA = os.path.join(DB_DIR, "schema.sql")
ROLES = os.path.join(DB_DIR, "roles.sql")
# The Lane Matt -> Lane Nick infra handoff. db/roles.sql is Nick-only (CONTRIBUTING.md
# § Two-lane contract), so a freshly-appended RLS-EXEMPT table's GRANT lands here as an OPEN
# REQ first and in roles.sql only once Nick applies it — the documented ordered cross-lane
# sequence (schema append -> grant). The grant-gate below accepts that pending state so a
# schema-append PR is CI-green, WITHOUT going silent: the grant must exist in EITHER roles.sql
# OR a tracked REQ that names the table with a crm_app GRANT.
REQUESTS = os.path.join(os.path.dirname(__file__), "..", "..", "infra", "REQUESTS.md")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------- #
# Derivation — the table lists below come from the schema, not from this file.
# --------------------------------------------------------------------------- #
_CREATE_RE = re.compile(
    r"((?:^[ \t]*--[^\n]*\n)*)^CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);",
    re.S | re.M,
)


def _tables(sql: str) -> dict[str, dict]:
    """Every CREATE TABLE in schema.sql -> {name: {comment, body}}.

    `comment` is the contiguous `--` comment block immediately above the CREATE
    (where the RLS-EXEMPT marker lives, per the schema.sql convention).
    """
    out: dict[str, dict] = {}
    for m in _CREATE_RE.finditer(sql):
        out[m.group(2)] = {"comment": m.group(1), "body": m.group(3)}
    return out


def _has_mandatory_tenant_id(body: str) -> bool:
    return bool(re.search(r"tenant_id\s+uuid\s+(NOT NULL|PRIMARY KEY)", body))


def derive_tenant_tables(sql: str) -> list[str]:
    """Tenant tables = mandatory tenant_id, minus explicit RLS-EXEMPT markers."""
    return sorted(
        name
        for name, t in _tables(sql).items()
        if _has_mandatory_tenant_id(t["body"]) and "RLS-EXEMPT" not in t["comment"]
    )


def do_block_tenant_tables(sql: str) -> list[str]:
    """The schema's own source of truth: the tenant_tables array in the RLS DO block."""
    m = re.search(r"tenant_tables text\[\] := ARRAY\[(.*?)\];", sql, re.S)
    assert m, "RLS DO-block tenant_tables array not found in schema.sql"
    return sorted(re.findall(r"'(\w+)'", m.group(1)))


def derive_rls_exempt_tables(sql: str) -> list[str]:
    """RLS-EXEMPT tables = every CREATE TABLE carrying an explicit `-- RLS-EXEMPT` marker.

    These are the pre-tenant tables (signup accounts/ledger, the workspace-key pool, lead
    capture). They are deliberately NOT in the tenant_tables array, so access control is
    GRANT-based, not RLS — which means they need EXPLICIT crm_app GRANTs (the same fresh-load
    gap as the tenant tables) and the tenant-table grant gate never covers them."""
    return sorted(
        name for name, t in _tables(sql).items() if "RLS-EXEMPT" in t["comment"]
    )


_SCHEMA_SQL = _read(SCHEMA)
TENANT_TABLES = derive_tenant_tables(_SCHEMA_SQL)
RLS_EXEMPT_TABLES = derive_rls_exempt_tables(_SCHEMA_SQL)


@pytest.mark.unit
def test_schema_parses():
    """schema.sql is valid Postgres grammar (raises pglast.parser.ParseError otherwise)."""
    stmts = pglast.parse_sql(_read(SCHEMA))
    assert len(stmts) > 0


@pytest.mark.unit
def test_roles_parses():
    stmts = pglast.parse_sql(_read(ROLES))
    assert len(stmts) > 0


@pytest.mark.unit
def test_derivation_sees_the_full_schema():
    """The derivation regex actually matched the schema's tables (guards the gate's own
    machinery: a formatting change that blinds the regex must fail loudly, not shrink
    the gate to an empty list)."""
    tables = _tables(_SCHEMA_SQL)
    assert len(tables) >= 13, f"only matched {sorted(tables)} — derivation regex went blind?"
    # Known anchors on both sides of the split:
    assert "documents" in TENANT_TABLES
    assert "tenant_workspaces" in TENANT_TABLES
    assert "tenant_settings" in TENANT_TABLES
    assert "accounts" not in TENANT_TABLES        # RLS-EXEMPT (pre-tenant)
    assert "stripe_events" not in TENANT_TABLES   # RLS-EXEMPT (pre-tenant, no tenant scope)


@pytest.mark.unit
def test_do_block_array_matches_derived_tenant_tables():
    """The schema's tenant_tables array == the derived set. A new tenant table missing from
    the array (no RLS policy!) or a stale array entry both fail here."""
    assert do_block_tenant_tables(_SCHEMA_SQL) == TENANT_TABLES


@pytest.mark.unit
def test_every_rls_exempt_table_carries_a_reason():
    """Pre-tenant tables opt out of RLS only via an explicit `-- RLS-EXEMPT: <reason>` marker."""
    for name, t in _tables(_SCHEMA_SQL).items():
        if name in TENANT_TABLES:
            continue
        assert "RLS-EXEMPT" in t["comment"], (
            f"{name} is not in the tenant tables but has no RLS-EXEMPT marker — "
            "either add it to RLS (tenant_tables array + FORCE) or mark the exemption"
        )


@pytest.mark.unit
@pytest.mark.parametrize("table", TENANT_TABLES)
def test_every_tenant_table_has_mandatory_tenant_id(table):
    body = _tables(_SCHEMA_SQL)[table]["body"]
    assert _has_mandatory_tenant_id(body), (
        f"{table} missing a mandatory tenant_id (uuid NOT NULL / uuid PRIMARY KEY)"
    )


@pytest.mark.unit
@pytest.mark.parametrize("table", TENANT_TABLES)
def test_table_enables_and_forces_rls(table):
    """Without FORCE, the table owner bypasses RLS — tenant isolation silently fails."""
    sql = _SCHEMA_SQL
    assert re.search(rf"ALTER TABLE {table}\s+ENABLE ROW LEVEL SECURITY", sql), \
        f"{table} never ENABLEs RLS"
    assert re.search(rf"ALTER TABLE {table}\s+FORCE ROW LEVEL SECURITY", sql), \
        f"{table} never FORCEs RLS (owner would bypass the policy)"


@pytest.mark.unit
def test_policy_uses_app_current_tenant_guc():
    """The policy must key on app.current_tenant (the GUC the app/worker SET per connection).

    The policy is generated inside a DO/format() block, so single quotes are doubled in the
    source. Normalize '' -> ' before matching so we test intent, not escaping.
    """
    sql = _read(SCHEMA).replace("''", "'")
    assert "tenant_isolation" in sql
    assert "current_setting('app.current_tenant'" in sql
    # both USING and WITH CHECK present (read AND write are scoped)
    assert "USING (tenant_id = current_setting('app.current_tenant'" in sql
    assert "WITH CHECK (tenant_id = current_setting('app.current_tenant'" in sql


@pytest.mark.unit
def test_app_role_is_non_bypass():
    """crm_app must be NOBYPASSRLS / NOSUPERUSER or policies no-op."""
    roles = _read(ROLES)
    assert "NOBYPASSRLS" in roles
    assert "NOSUPERUSER" in roles
    assert re.search(r"GRANT SELECT, INSERT, UPDATE, DELETE", roles)


# --------------------------------------------------------------------------- #
# Grant-surface gate (static): every tenant table must be granted to crm_app in
# roles.sql, and the audit-trail tables must stay append-only.
# --------------------------------------------------------------------------- #
def _pending_grant_tables() -> dict[str, set[str]]:
    """{table: privileges} for RLS-EXEMPT tables whose crm_app GRANT is still PENDING in an
    OPEN/IN-PROGRESS REQ in infra/REQUESTS.md (roles.sql is Nick-only — the schema-append PR
    files the REQ, Nick lands the grant later, per the ordered cross-lane sequence). Only
    requests whose Status is OPEN or IN-PROGRESS count; a DONE/REJECTED req does NOT excuse a
    missing roles.sql grant (by then the grant must really be in roles.sql)."""
    try:
        with open(REQUESTS, "r", encoding="utf-8") as f:
            md = f.read()
    except FileNotFoundError:
        return {}
    pending: dict[str, set[str]] = {}
    # Split into per-REQ blocks (### REQ-...). A block "grants" a table if its body has a
    # `GRANT <privs> ON <table> TO crm_app` AND its Status line is OPEN/IN-PROGRESS.
    blocks = re.split(r"^### REQ-", md, flags=re.M)
    grant_re = re.compile(r"GRANT\s+([A-Z, ]+?)\s+ON\s+(\w+)\s+TO\s+crm_app")
    for block in blocks[1:]:
        status_m = re.search(r"\*\*Status:\*\*\s*([A-Za-z-]+)", block)
        status = status_m.group(1).upper() if status_m else ""
        if status not in ("OPEN", "IN-PROGRESS"):
            continue
        for privs, table in grant_re.findall(block):
            privset = {p.strip() for p in privs.split(",") if p.strip()}
            pending.setdefault(table, set()).update(privset)
    return pending


def _granted_tables(roles_sql: str) -> dict[str, set[str]]:
    """{table: union of privileges GRANTed to crm_app} from roles.sql (REVOKEs subtracted,
    in file order — the effective fresh-load surface)."""
    grants: dict[str, set[str]] = {}
    stmt_re = re.compile(
        r"^\s*(GRANT|REVOKE)\s+([A-Z, ]+?)\s+ON\s+([\w, \n]+?)\s+(?:TO|FROM)\s+crm_app",
        re.M,
    )
    # Strip comments so commented-out examples never count.
    src = re.sub(r"--[^\n]*", "", roles_sql)
    for verb, privs, tables in stmt_re.findall(src):
        privset = {p.strip() for p in privs.split(",") if p.strip()}
        for table in (t.strip() for t in tables.split(",") if t.strip()):
            if verb == "GRANT":
                grants.setdefault(table, set()).update(privset)
            else:
                grants.setdefault(table, set()).difference_update(privset)
    return grants


@pytest.mark.unit
def test_every_tenant_table_is_granted_to_crm_app():
    """The fresh-load GRANT gap: ALTER DEFAULT PRIVILEGES only covers tables created AFTER
    roles.sql runs, and schema.sql runs first — so every tenant table needs an explicit GRANT
    or crm_app has ZERO privileges on it on a fresh load (tenant_workspaces/tenant_settings
    had exactly this hole)."""
    grants = _granted_tables(_read(ROLES))
    for table in TENANT_TABLES:
        effective = grants.get(table, set())
        assert {"SELECT", "INSERT"} <= effective, (
            f"{table} has no usable crm_app GRANT in roles.sql (fresh-load gap): {effective}"
        )


@pytest.mark.unit
def test_audit_trail_grants_are_append_only():
    """approvals: the Greenlight decided-flip needs UPDATE, but never DELETE.
    traces: strictly append-only — neither UPDATE nor DELETE."""
    grants = _granted_tables(_read(ROLES))
    assert "DELETE" not in grants["approvals"], "crm_app must not DELETE approvals (audit trail)"
    assert "UPDATE" in grants["approvals"], "Greenlight needs UPDATE for the decided flip"
    assert "DELETE" not in grants["traces"], "crm_app must not DELETE traces (audit trail)"
    assert "UPDATE" not in grants["traces"], "traces are append-only — no UPDATE"
    # Per-tenant control rows: upserted, never deleted by the app.
    for table in ("tenant_workspaces", "tenant_settings"):
        assert "DELETE" not in grants[table], f"crm_app must not DELETE {table}"
        assert {"SELECT", "INSERT", "UPDATE"} <= grants[table]


@pytest.mark.unit
def test_rls_exempt_derivation_sees_the_pre_tenant_tables():
    """Guard the derivation's own machinery: the RLS-EXEMPT set must include the known
    pre-tenant tables, so a formatting change that blinds the marker fails loudly instead of
    shrinking the gate below to an empty list."""
    for table in ("accounts", "stripe_events", "workspace_keys", "leads"):
        assert table in RLS_EXEMPT_TABLES, (
            f"{table} expected in the RLS-EXEMPT set: {RLS_EXEMPT_TABLES}"
        )


@pytest.mark.unit
@pytest.mark.parametrize("table", RLS_EXEMPT_TABLES)
def test_every_rls_exempt_table_is_granted_to_crm_app(table):
    """RLS-EXEMPT tables are GRANT-gated, not RLS-gated — so the fresh-load zero-privilege
    gap bites them too (schema.sql creates them before roles.sql, so ALTER DEFAULT PRIVILEGES
    never covers them). The tenant-table grant gate skips these, so without this assertion a
    fresh deploy permission-denies key consumption (workspace_keys) and lead capture (leads).
    Every pre-tenant table the app touches needs at minimum SELECT+INSERT.

    A freshly-appended table whose grant is still PENDING in an OPEN/IN-PROGRESS REQUESTS.md
    REQ is accepted here (roles.sql is Nick-only — the schema-append PR files the REQ, Nick
    lands the roles.sql grant later). The gate never goes silent: the grant must exist in
    roles.sql OR be tracked in a pending REQ — not nowhere."""
    effective = _granted_tables(_read(ROLES)).get(table, set())
    if {"SELECT", "INSERT"} <= effective:
        return
    pending = _pending_grant_tables().get(table, set())
    assert {"SELECT", "INSERT"} <= (effective | pending), (
        f"{table} (RLS-EXEMPT) has no usable crm_app GRANT in roles.sql and no pending "
        f"REQUESTS.md grant (fresh-load gap): roles={effective} pending={pending}"
    )


@pytest.mark.unit
def test_rls_exempt_tables_are_not_deletable_by_app():
    """The pre-tenant tables are records the app parks/flips/appends but never erases:
    accounts (parked/flipped), stripe_events (idempotency ledger), workspace_keys (key
    allocation audit trail), leads (append-only capture). None may carry a crm_app DELETE."""
    grants = _granted_tables(_read(ROLES))
    for table in RLS_EXEMPT_TABLES:
        assert "DELETE" not in grants.get(table, set()), (
            f"crm_app must not DELETE {table} (pre-tenant record / audit trail)"
        )
    # leads is strictly append-only: no UPDATE either (a captured lead is never edited).
    assert "UPDATE" not in grants.get("leads", set()), "leads is append-only — no UPDATE"
    # workspace_keys needs UPDATE for the atomic consume (available -> consumed).
    assert "UPDATE" in grants.get("workspace_keys", set()), (
        "workspace_keys needs UPDATE for the atomic pool-consume"
    )


# --------------------------------------------------------------------------- #
# Composite same-tenant FKs — FK checks run as the table owner (RLS does NOT
# scope them), so single-column FKs to companies(id)/contacts(id)/deals(id)
# could validate against ANOTHER tenant's parent row. The schema must carry the
# composite (tenant_id, id) constraints instead.
# --------------------------------------------------------------------------- #
COMPOSITE_FKS = [
    ("contacts", "company_id", "companies"),
    ("deals", "company_id", "companies"),
    ("deals", "contact_id", "contacts"),
    ("activities", "contact_id", "contacts"),
    ("activities", "deal_id", "deals"),
]


@pytest.mark.unit
@pytest.mark.parametrize("child,col,parent", COMPOSITE_FKS)
def test_child_fk_is_composite_same_tenant(child, col, parent):
    pattern = (
        rf"ALTER TABLE {child} ADD CONSTRAINT \w+\s+"
        rf"FOREIGN KEY \(tenant_id, {col}\) REFERENCES {parent} \(tenant_id, id\)"
    )
    assert re.search(pattern, _SCHEMA_SQL), (
        f"{child}.{col} -> {parent} must be a composite (tenant_id, {col}) FK — "
        "a single-column FK validates across tenants (FK checks bypass RLS)"
    )


@pytest.mark.unit
@pytest.mark.parametrize("parent", sorted({p for _, _, p in COMPOSITE_FKS}))
def test_fk_parent_has_tenant_scoped_unique_key(parent):
    assert re.search(
        rf"ALTER TABLE {parent} ADD CONSTRAINT \w+ UNIQUE \(tenant_id, id\)", _SCHEMA_SQL
    ), f"{parent} needs UNIQUE (tenant_id, id) to anchor the composite FKs"


@pytest.mark.unit
@pytest.mark.parametrize("child,col,parent", COMPOSITE_FKS)
def test_single_column_fk_is_dropped(child, col, parent):
    """The inline single-column FK (cross-tenant capable) must be retired by the migration."""
    assert re.search(
        rf"ALTER TABLE {child}\s+DROP CONSTRAINT IF EXISTS {child}_{col}_fkey", _SCHEMA_SQL
    ), f"{child}.{col}: the single-column {child}_{col}_fkey is never dropped"
