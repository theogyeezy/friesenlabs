"""Unit: the prod-enablement guard for the TESTING-ONLY internal Stripe bypass (Medium #8).

Config.assert_bypass_not_enabled_in_prod() (run from shared.config.load() at startup) must REFUSE
to boot when the stage is prod AND SIGNUP_INTERNAL_BYPASS_DOMAINS is set, unless the explicit
SIGNUP_INTERNAL_BYPASS_ALLOW_IN_PROD escape hatch is on. Inert in non-prod and when no bypass
domain is configured.

The Config dataclass reads env at IMPORT time for its defaults, so these tests construct Config(...)
with explicit fields (the same style as test_shared_config.py) to exercise the guard deterministically,
plus a reload-based check that load() actually invokes it at startup.
"""
import importlib

import pytest

from shared.config import Config


def _cfg(**kw):
    return Config(**kw)


def test_prod_with_bypass_domains_refuses():
    cfg = _cfg(environment="prod", signup_internal_bypass_domains="friesenlabs.com")
    with pytest.raises(RuntimeError) as ei:
        cfg.assert_bypass_not_enabled_in_prod()
    assert "PROD" in str(ei.value)


@pytest.mark.parametrize("stage", ["prod", "production", "PROD", " Production "])
def test_prod_aliases_all_trip_the_guard(stage):
    cfg = _cfg(environment=stage, signup_internal_bypass_domains="x.com")
    assert cfg.is_prod() is True
    with pytest.raises(RuntimeError):
        cfg.assert_bypass_not_enabled_in_prod()


def test_escape_hatch_allows_prod_bypass():
    cfg = _cfg(environment="prod", signup_internal_bypass_domains="friesenlabs.com",
               signup_internal_bypass_allow_in_prod=True)
    cfg.assert_bypass_not_enabled_in_prod()       # does not raise
    assert cfg.internal_bypass_domain_set() == frozenset({"friesenlabs.com"})


def test_non_prod_with_bypass_is_allowed():
    cfg = _cfg(environment="dev", signup_internal_bypass_domains="friesenlabs.com")
    cfg.assert_bypass_not_enabled_in_prod()       # dev/staging may use the bypass freely


def test_unset_environment_treated_as_non_prod():
    cfg = _cfg(environment="", signup_internal_bypass_domains="friesenlabs.com")
    assert cfg.is_prod() is False
    cfg.assert_bypass_not_enabled_in_prod()       # guard inert until the prod stage is named


def test_prod_without_bypass_domains_is_fine():
    cfg = _cfg(environment="prod", signup_internal_bypass_domains="")
    assert cfg.is_prod() is True
    cfg.assert_bypass_not_enabled_in_prod()
    assert cfg.internal_bypass_domain_set() == frozenset()


# --- load() actually invokes the guard at startup (the "raises at startup" requirement) -------

def test_load_raises_when_prod_bypass_set(monkeypatch):
    monkeypatch.setenv("UPLIFT_ENVIRONMENT", "prod")
    monkeypatch.setenv("SIGNUP_INTERNAL_BYPASS_DOMAINS", "friesenlabs.com")
    monkeypatch.delenv("SIGNUP_INTERNAL_BYPASS_ALLOW_IN_PROD", raising=False)
    import shared.config as config
    importlib.reload(config)        # re-read env into the import-time dataclass defaults
    try:
        with pytest.raises(RuntimeError):
            config.load()
    finally:
        # Restore the module to the ambient (clean) env so later tests see normal defaults.
        for k in ("UPLIFT_ENVIRONMENT", "SIGNUP_INTERNAL_BYPASS_DOMAINS"):
            monkeypatch.delenv(k, raising=False)
        importlib.reload(config)


def test_load_escape_hatch_allows_startup(monkeypatch):
    monkeypatch.setenv("UPLIFT_ENVIRONMENT", "prod")
    monkeypatch.setenv("SIGNUP_INTERNAL_BYPASS_DOMAINS", "friesenlabs.com")
    monkeypatch.setenv("SIGNUP_INTERNAL_BYPASS_ALLOW_IN_PROD", "true")
    import shared.config as config
    importlib.reload(config)
    try:
        cfg = config.load()         # does not raise
        assert cfg.internal_bypass_domain_set() == frozenset({"friesenlabs.com"})
    finally:
        for k in ("UPLIFT_ENVIRONMENT", "SIGNUP_INTERNAL_BYPASS_DOMAINS",
                  "SIGNUP_INTERNAL_BYPASS_ALLOW_IN_PROD"):
            monkeypatch.delenv(k, raising=False)
        importlib.reload(config)
