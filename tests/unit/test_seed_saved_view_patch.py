"""Unit: scripts/seed_saved_view_patch.py — the #125 demo saved-view patch.

Proves, fully offline:
  - importing the script has no side effects (no boto3/psycopg2 import, no env needed);
  - the embedded SPEC validates against shared/schemas/view_spec.schema.json AND references
    only members that really exist in semantic/model/cubes/ (the web sample's
    `Deals.totalValue` mistake can't recur);
  - the insert is tenant-scoped (SET LOCAL app.current_tenant) and idempotent
    (WHERE NOT EXISTS guard; second run is a reported no-op);
  - an invalid spec aborts before any DB work.
"""
import importlib.util
import os
import re
import sys
from unittest import mock

import pytest

from shared import view_spec

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPT = os.path.join(_ROOT, "scripts", "seed_saved_view_patch.py")
_CUBES_DIR = os.path.join(_ROOT, "semantic", "model", "cubes")


def _load():
    spec = importlib.util.spec_from_file_location("seed_saved_view_patch", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _catalog_members() -> set[str]:
    """Extract every `Cube.member` actually defined in semantic/model/cubes/*.js."""
    members: set[str] = set()
    block_re = re.compile(r"(measures|dimensions):\s*\{(.*?)\n\s*\}", re.S)
    key_re = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*):\s*\{", re.M)
    for fname in os.listdir(_CUBES_DIR):
        if not fname.endswith(".js"):
            continue
        with open(os.path.join(_CUBES_DIR, fname), encoding="utf-8") as f:
            src = f.read()
        cube = re.search(r"cube\('([A-Za-z][A-Za-z0-9_]*)'", src).group(1)
        for _, body in block_re.findall(src):
            for key in key_re.findall(body):
                members.add(f"{cube}.{key}")
    return members


# ---------------------------------------------------------------- import safety

@pytest.mark.unit
def test_import_has_no_side_effects(monkeypatch):
    # No env, no creds: import must still succeed and must not pull in DB/AWS clients.
    for var in ("TENANT_ID", "UPLIFT_DB_URL", "CRM_APP_SECRET_ARN", "DB_HOST"):
        monkeypatch.delenv(var, raising=False)
    before = set(sys.modules)
    mod = _load()
    assert callable(mod.main)
    assert "boto3" not in (set(sys.modules) - before)
    assert mod.VIEW_ID == "demo_pipeline"  # what DashboardView fetches by default


# ---------------------------------------------------------------- spec validity

@pytest.mark.unit
def test_spec_is_schema_valid_and_uses_only_real_cube_members():
    mod = _load()
    # Schema + the script's own member allowlist (the script's pre-write gate).
    view_spec.validate(mod.SPEC, allowed_members=mod.CUBE_MEMBERS)
    # And that allowlist is honest: every member the spec references exists in the
    # real Cube model files (catches drift like the web sample's Deals.totalValue).
    catalog = _catalog_members()
    assert "Deals.pipeline_value" in catalog  # sanity: extraction works
    referenced = set(view_spec._iter_members(mod.SPEC))
    assert referenced <= catalog, f"spec references unknown members: {referenced - catalog}"


@pytest.mark.unit
def test_spec_view_id_matches_command_center_default():
    mod = _load()
    dashboard = os.path.join(_ROOT, "web", "src", "api", "DashboardView.tsx")
    with open(dashboard, encoding="utf-8") as f:
        assert 'viewId = "demo_pipeline"' in f.read()
    assert mod.SPEC["view_id"] == "demo_pipeline"


# ---------------------------------------------------------------- insert behavior

def _fake_conn(insert_rowcount=1):
    conn = mock.MagicMock()
    cur = mock.MagicMock()
    conn.cursor.return_value = cur

    def _execute(sql, params=None):
        cur.rowcount = insert_rowcount if "INSERT" in sql else -1

    cur.execute.side_effect = _execute
    return conn, cur


@pytest.mark.unit
def test_main_inserts_tenant_scoped_and_commits(monkeypatch, capsys):
    monkeypatch.setenv("TENANT_ID", "11111111-1111-1111-1111-111111111111")
    mod = _load()
    conn, cur = _fake_conn(insert_rowcount=1)

    assert mod.main(connect=lambda: conn) == 0

    calls = cur.execute.call_args_list
    # First statement of the transaction: SET LOCAL with the env tenant (RLS scope).
    set_sql, set_params = calls[0][0]
    assert "SET LOCAL app.current_tenant" in set_sql
    assert set_params == ("11111111-1111-1111-1111-111111111111",)
    # Then exactly one guarded insert, parameterized — never string-built.
    ins_sql, ins_params = calls[1][0]
    assert "INSERT INTO saved_views" in ins_sql
    assert "WHERE NOT EXISTS" in ins_sql
    assert ins_params["tenant"] == "11111111-1111-1111-1111-111111111111"
    assert ins_params["view_id"] == "demo_pipeline"
    assert len(calls) == 2
    conn.commit.assert_called_once()
    conn.rollback.assert_not_called()
    conn.close.assert_called_once()
    assert "seeded saved view 'demo_pipeline'" in capsys.readouterr().out


@pytest.mark.unit
def test_main_is_idempotent_second_run_noop(monkeypatch, capsys):
    monkeypatch.setenv("TENANT_ID", "t-1")
    mod = _load()
    conn, _ = _fake_conn(insert_rowcount=0)  # guard matched: row already exists

    assert mod.main(connect=lambda: conn) == 0

    conn.commit.assert_called_once()  # still commits the no-op transaction cleanly
    assert "already present" in capsys.readouterr().out


@pytest.mark.unit
def test_invalid_spec_aborts_before_any_db_work(monkeypatch):
    monkeypatch.setenv("TENANT_ID", "t-1")
    mod = _load()
    mod.SPEC["layout"][0]["metric"] = "Deals.not_real"  # poison the module copy
    connect = mock.MagicMock()

    with pytest.raises(view_spec.ValidationError):
        mod.main(connect=connect)
    connect.assert_not_called()


@pytest.mark.unit
def test_db_error_rolls_back_and_reraises(monkeypatch):
    monkeypatch.setenv("TENANT_ID", "t-1")
    mod = _load()
    conn = mock.MagicMock()
    conn.cursor.return_value.execute.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError):
        mod.main(connect=lambda: conn)
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
    conn.close.assert_called_once()
