"""Unit: the worker TOOLS list is reconciled with the trusted registry (no registered-but-unserved
CRM-write drift), and the worker warns loudly when it boots with no data-plane DSN.

Companion to test_worker_roster_parity.py (which pins served == granted). Here we pin the SPECIFIC
reconciliation intent: the full CRM-write suite the registry defines is actually served, and
send_email stays the deliberate registry-only exception (classified, never served).
"""
import logging

import pytest

from agents.tools.registry import TOOL_REGISTRY
from worker import worker

# The full CRM-write suite the trusted registry defines as real ALWAYS_ASK Greenlight tools — the
# same actions the Greenlight appliers execute post-approval. None may be registered-but-unserved.
CRM_WRITE_SUITE = {"update_deal", "update_contact", "create_activity", "create_deal"}


def _served() -> set[str]:
    return {t.name for t in worker.TOOLS}


@pytest.mark.unit
def test_full_crm_write_suite_is_served():
    served = _served()
    missing = CRM_WRITE_SUITE - served
    assert not missing, f"registry CRM-write tools registered-but-unserved by the worker: {sorted(missing)}"


@pytest.mark.unit
def test_send_email_is_registered_but_deliberately_unserved():
    # send_email exists in the registry ONLY so the action gate can classify it side-effecting;
    # no agent grants it and the real send is the post-approval api/control path. It must NOT be
    # served by the worker (serving it would be unreachable, creds-adjacent dead weight).
    assert "send_email" in TOOL_REGISTRY
    assert "send_email" not in _served()


@pytest.mark.unit
def test_every_served_tool_is_a_registry_tool():
    # The worker only ever serves trusted-registry tools (the gate derives side-effecting truth
    # from the tool class; an off-registry server would bypass that contract).
    assert _served() <= set(TOOL_REGISTRY)


@pytest.mark.unit
def test_build_clients_warns_when_no_dsn(monkeypatch, caplog):
    # Strip every DSN-bearing env var so dsn_from_env() returns falsy -> rag/db/greenlight None.
    for var in ("UPLIFT_DB_URL", "DB_USER", "DB_PASS", "DB_HOST", "DB_NAME", "DB_PORT"):
        monkeypatch.delenv(var, raising=False)
    # Keep the rest of build_clients_from_env inert (no cube/cortex/anthropic env).
    for var in ("CUBE_ENDPOINT", "CUBEJS_API_SECRET_VALUE", "CORTEX_S3_BUCKET",
                "CORTEX_LOCAL_DIR", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    with caplog.at_level(logging.WARNING, logger=worker.log.name):
        clients = worker.build_clients_from_env()
    assert clients["rag"] is None and clients["db"] is None
    assert any("no crm_app DSN" in r.message for r in caplog.records), \
        "expected a loud startup warning when the worker boots without a data-plane DSN"
