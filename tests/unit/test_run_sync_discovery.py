"""Unit: INGEST_TENANTS="auto" — vault-slot tenant discovery (ingest/run_sync.py).

All offline (fake Secrets Manager client). Proves the connect->sync loop closes:
  * discover_tenants lists the uplift/{tenant}/{source} namespace, names only —
    filtered to the requested source, deduped + sorted, paginated, deletion-scheduled
    slots skipped, non-slot names in the namespace ignored
  * resolve_tenants: "auto" offline -> honest empty (warn, no boto3); a comma list
    and explicit --tenant flags behave exactly as before
"""
import argparse

import pytest

from ingest.run_sync import discover_tenants, resolve_tenants
from shared.config import ENV_INGEST_REAL_STORES, ENV_INGEST_TENANTS

pytestmark = pytest.mark.unit


class FakeSm:
    """Fake secretsmanager: serves list_secrets pages; records the filters used."""

    def __init__(self, pages):
        self._pages = pages   # list of SecretList batches
        self.calls = []

    def list_secrets(self, **kwargs):
        self.calls.append(kwargs)
        idx = int(kwargs.get("NextToken", 0))
        page = {"SecretList": self._pages[idx]}
        if idx + 1 < len(self._pages):
            page["NextToken"] = str(idx + 1)
        return page


def _args(tenant=None, source="hubspot"):
    return argparse.Namespace(tenant=tenant, all=tenant is None, source=source)


def test_discover_filters_to_source_and_skips_nonslots():
    sm = FakeSm([[
        {"Name": "uplift/t-b/hubspot"},
        {"Name": "uplift/t-a/hubspot"},
        {"Name": "uplift/t-a/stripe"},          # other source — not this run's set
        {"Name": "uplift/env-id"},              # 2 segments: not a connector slot
        {"Name": "uplift/demo-user"},           # ditto
        {"Name": "other/t-x/hubspot"},          # foreign namespace
        {"Name": "uplift/t-c/hubspot/extra"},   # 4 segments: not a slot
    ]])
    assert discover_tenants("hubspot", client=sm) == ["t-a", "t-b"]  # sorted, deduped
    # The list rides a name-prefix filter (metadata only — never a value read).
    assert sm.calls[0]["Filters"] == [{"Key": "name", "Values": ["uplift/"]}]


def test_discover_paginates_and_skips_deletion_scheduled():
    sm = FakeSm([
        [{"Name": "uplift/t-a/hubspot"},
         {"Name": "uplift/t-gone/hubspot", "DeletedDate": "2026-06-11T00:00:00Z"}],
        [{"Name": "uplift/t-b/hubspot"},
         {"Name": "uplift/t-a/hubspot"}],  # duplicate across pages — deduped
    ])
    assert discover_tenants("hubspot", client=sm) == ["t-a", "t-b"]
    assert len(sm.calls) == 2  # followed NextToken


def test_resolve_auto_offline_is_honest_empty(monkeypatch, caplog):
    """auto without INGEST_REAL_STORES: no boto3, no invented tenants — warn + []
    (main() then logs 'nothing to do' and exits 0, the safe-schedule posture)."""
    monkeypatch.delenv(ENV_INGEST_REAL_STORES, raising=False)
    monkeypatch.setenv(ENV_INGEST_TENANTS, "auto")
    with caplog.at_level("WARNING"):
        assert resolve_tenants(_args()) == []
    assert "auto" in caplog.text


def test_resolve_comma_list_and_explicit_flags_unchanged(monkeypatch):
    monkeypatch.setenv(ENV_INGEST_TENANTS, " t-a, t-b ,,t-a ")
    assert resolve_tenants(_args()) == ["t-a", "t-b"]
    # Explicit --tenant flags always win and never consult the env.
    monkeypatch.setenv(ENV_INGEST_TENANTS, "auto")
    assert resolve_tenants(_args(tenant=["t-z", "t-z", "t-q"])) == ["t-z", "t-q"]
