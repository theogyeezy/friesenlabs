"""Unit: scripts/verify_agent_plane.py is import-safe and offline CI can run it harmlessly.

The live legs are gated behind UPLIFT_LIVE_VERIFY=1 + required creds; without them the script
must print a per-step PLAN and exit 0 — never touching anthropic, AWS, or a DB.
"""
import importlib.util
import os
import sys

import pytest

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts", "verify_agent_plane.py",
)

_GATE_VARS = (
    "UPLIFT_LIVE_VERIFY", "UPLIFT_VERIFY_TENANT_ID", "UPLIFT_VERIFY_ALLOW_PROVISION",
    "UPLIFT_VERIFY_EMAIL_TO", "ANTHROPIC_API_KEY", "UPLIFT_ENV_ID",
    "UPLIFT_DB_URL", "DB_USER", "DB_PASS", "DB_HOST",
)


def _load():
    spec = importlib.util.spec_from_file_location("verify_agent_plane", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def offline_env(monkeypatch):
    for var in _GATE_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.mark.unit
def test_import_has_no_side_effects(offline_env):
    # Importing must not build clients/pools or hit the network — and must not poison
    # sys.modules for the rest of the suite.
    before = set(sys.modules)
    mod = _load()
    assert callable(mod.main)
    assert "anthropic" not in (set(sys.modules) - before)


@pytest.mark.unit
def test_offline_plan_mode_exits_zero_and_prints_every_step(offline_env, capsys):
    mod = _load()
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "OFFLINE PLAN MODE" in out
    for name, _ in mod.STEPS:
        assert f"[PLAN] {name}" in out
    assert "LIVE mode" not in out


@pytest.mark.unit
def test_live_flag_without_creds_still_plans_and_names_the_missing_env(offline_env, monkeypatch, capsys):
    monkeypatch.setenv("UPLIFT_LIVE_VERIFY", "1")
    mod = _load()
    assert mod.main() == 0  # creds absent -> still offline, still exit 0
    out = capsys.readouterr().out
    assert "OFFLINE PLAN MODE" in out
    for name in ("ANTHROPIC_API_KEY", "UPLIFT_ENV_ID", "UPLIFT_VERIFY_TENANT_ID"):
        assert name in out


@pytest.mark.unit
def test_master_switch_is_strict(offline_env, monkeypatch, capsys):
    # Anything but exactly 'true'/'1' fails CLOSED (same _switch_env contract as the other
    # master switches) — 'True', 'yes', ' 1 ' must all stay offline.
    mod = _load()
    for junk in ("True", "yes", " 1 ", "on"):
        monkeypatch.setenv("UPLIFT_LIVE_VERIFY", junk)
        assert mod.main() == 0
        assert "OFFLINE PLAN MODE" in capsys.readouterr().out


@pytest.mark.unit
def test_draft_body_passes_the_compliance_email_check(offline_env):
    # The verify draft must exercise Greenlight, not the CAN-SPAM compliance block.
    mod = _load()
    assert "unsubscribe" in mod.DRAFT_BODY.lower()


# ---------------------------------------------------------------- deterministic live legs
# Steps [1] and [4]-[6] never touch Anthropic — provable offline with the in-memory stores.

def _env():
    return {"tenant_id": "tenant-verify", "api_key": "unused", "env_id": "env_x"}


@pytest.mark.unit
def test_workspace_step_load_refuse_stub_and_provision_gate(offline_env):
    mod = _load()
    stores = mod._build_stores(None)
    env = _env()
    report = mod.Report()

    # No row + allow-provision off -> FAIL with actionable guidance (never silently provisions).
    assert mod._step_workspace(report, stores, env) is None
    assert report.results[-1]["status"] == "FAIL"
    assert "UPLIFT_VERIFY_ALLOW_PROVISION" in report.results[-1]["detail"]

    # Complete persisted row -> PASS, returned for the downstream steps.
    stores["workspace_store"].upsert("tenant-verify", None, "env_live", "coord_live")
    row = mod._step_workspace(report, stores, env)
    assert row["coordinator_id"] == "coord_live"
    assert report.results[-1]["status"] == "PASS"

    # Offline 'stub-' placeholder ids -> FAIL (same refusal the asgi factory makes).
    stores["workspace_store"].upsert("tenant-verify", None, "stub-env", "stub-coord")
    assert mod._step_workspace(report, stores, env) is None
    assert "stub" in report.results[-1]["detail"]


@pytest.mark.unit
def test_greenlight_approve_execute_pipeline_offline(offline_env):
    mod = _load()
    stores = mod._build_stores(None)
    env = _env()
    report = mod.Report()

    # [4] pending approval, no execution, exactly one pending_approval trace.
    approval_id = mod._step_greenlight(report, stores, env)
    assert approval_id is not None
    # [5] human approve.
    approved = mod._step_approve(report, stores, env, approval_id)
    assert approved["status"] == "approved"
    # [6] gated dispatch: executor once, one executed trace, draft-only held.
    mod._step_execute(report, stores, env, approved)

    assert {r["step"]: r["status"] for r in report.results} == {
        "greenlight": "PASS", "approve": "PASS", "execute": "PASS",
    }
    # Cleanup leaves the tenant's queue empty (denies the dispatch's fresh draft proposal too).
    mod._cleanup(stores, env)
    assert stores["greenlight"].list_pending("tenant-verify") == []
