"""Unit: shared/config.py readers stitched in by the backend-stitch lane.

CONTRIBUTING.md §Env-var contract: shared/config.py is the single source of truth for every
env var the app reads — Lane Nick mirrors names FROM it into task defs. This file pins the
three names the cross-lane PRs introduced:

  * CONTROL_GLOBAL_OPERATOR_TENANTS (#186) — NEW reader here; must parse exactly like the
    in-route `_global_operators` in api/routes_control.py (one semantic, two readers would
    drift into an authz bug);
  * SIGNUP_INTERNAL_BYPASS_DOMAINS (#181 flagged it missing; #188 landed the Config field) —
    asserted present so the name can never silently vanish;
  * CORTEX_SIGNING_KEY (#194) — likewise asserted present.
"""
from __future__ import annotations

import pytest

from shared import config as shared_config
from shared.config import (
    ENV_CONTROL_GLOBAL_OPERATOR_TENANTS,
    Config,
    control_global_operator_tenants,
)


# ---------------- CONTROL_GLOBAL_OPERATOR_TENANTS ----------------
@pytest.mark.unit
def test_unset_means_nobody_fail_closed(monkeypatch):
    monkeypatch.delenv(ENV_CONTROL_GLOBAL_OPERATOR_TENANTS, raising=False)
    assert control_global_operator_tenants() == frozenset()


@pytest.mark.unit
@pytest.mark.parametrize("junk", ["", "   ", ",", " , ,, "])
def test_empty_and_whitespace_parse_to_nobody(monkeypatch, junk):
    monkeypatch.setenv(ENV_CONTROL_GLOBAL_OPERATOR_TENANTS, junk)
    assert control_global_operator_tenants() == frozenset()


@pytest.mark.unit
def test_comma_separated_uuids_parse_stripped_no_case_folding(monkeypatch):
    monkeypatch.setenv(ENV_CONTROL_GLOBAL_OPERATOR_TENANTS,
                       " 11111111-1111-4111-8111-111111111111 ,ABC-tenant, ")
    got = control_global_operator_tenants()
    assert got == frozenset({"11111111-1111-4111-8111-111111111111", "ABC-tenant"})
    # No case folding: ids must match the verified claim byte-for-byte.
    assert "abc-tenant" not in got


@pytest.mark.unit
def test_read_at_call_time_rotation_needs_no_restart(monkeypatch):
    monkeypatch.setenv(ENV_CONTROL_GLOBAL_OPERATOR_TENANTS, "t-1")
    assert control_global_operator_tenants() == frozenset({"t-1"})
    monkeypatch.setenv(ENV_CONTROL_GLOBAL_OPERATOR_TENANTS, "t-2")
    assert control_global_operator_tenants() == frozenset({"t-2"})


@pytest.mark.unit
def test_name_matches_the_route_constant_one_env_var_two_readers_zero_drift():
    from api.routes_control import ENV_CONTROL_GLOBAL_OPERATORS
    assert ENV_CONTROL_GLOBAL_OPERATOR_TENANTS == ENV_CONTROL_GLOBAL_OPERATORS


@pytest.mark.unit
def test_parse_semantics_match_the_route_parser(monkeypatch):
    """The route's per-request `_global_operators` and the config reader must agree on every
    edge (strip, drop-empties, no folding) — a divergence is an authz bug, not a style nit."""
    from api.routes_control import _global_operators

    for raw in ("", " ", "a,b", " a , ,B,", "A-1,a-1"):
        monkeypatch.setenv(ENV_CONTROL_GLOBAL_OPERATOR_TENANTS, raw)
        assert control_global_operator_tenants() == frozenset(_global_operators()), raw


# ---------------- readers the cross-lane PRs require must exist ----------------
# NOTE: Config field defaults snapshot env at IMPORT time (plain `os.environ.get` defaults),
# so these construct with explicit kwargs — what matters here is that the FIELD + NAME exist,
# pinned, so the contract names can never silently vanish from the single source of truth.
@pytest.mark.unit
def test_signup_internal_bypass_domains_reader_exists():
    cfg = Config(signup_internal_bypass_domains=" Friesenlabs.com , other.io ")
    assert cfg.internal_bypass_domain_set() == frozenset({"friesenlabs.com", "other.io"})
    # The default is OFF: empty string parses to the empty set (no domain ever bypasses).
    assert Config(signup_internal_bypass_domains="").internal_bypass_domain_set() == frozenset()
    assert shared_config.ENV_SIGNUP_INTERNAL_BYPASS_DOMAINS == "SIGNUP_INTERNAL_BYPASS_DOMAINS"


@pytest.mark.unit
def test_cortex_signing_key_reader_exists():
    assert shared_config.ENV_CORTEX_SIGNING_KEY == "CORTEX_SIGNING_KEY"
    assert Config(cortex_signing_key="k-123").cortex_signing_key == "k-123"
    # The field exists with a safe ''/unset-style default shape (a str, never None).
    assert isinstance(Config().cortex_signing_key, str)
