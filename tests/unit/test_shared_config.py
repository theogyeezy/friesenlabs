"""Unit: shared config loads with sane defaults and never leaks raw secret values."""
import pytest

from shared import config


@pytest.mark.unit
def test_defaults():
    c = config.load()
    assert c.aws_region  # has a region
    assert c.project == "uplift"


@pytest.mark.unit
def test_secret_refs_are_names_not_values():
    c = config.load()
    # These are Secrets Manager *references*, not credentials.
    assert c.anthropic_api_key_secret.startswith("uplift/")
    assert not c.anthropic_api_key_secret.startswith("sk-ant-")


@pytest.mark.unit
def test_constants():
    assert config.EMBEDDING_DIM == 1024
    assert config.MA_BETA_HEADER == "managed-agents-2026-04-01"


@pytest.mark.unit
def test_internal_bypass_domains_default_empty_means_off():
    c = config.Config()   # default: the TESTING-ONLY Stripe bypass must not exist
    assert c.internal_bypass_domain_set() == frozenset()


@pytest.mark.unit
def test_internal_bypass_domains_parse_normalizes():
    c = config.Config(signup_internal_bypass_domains=" Friesenlabs.com, ,example.io ,")
    assert c.internal_bypass_domain_set() == frozenset({"friesenlabs.com", "example.io"})
