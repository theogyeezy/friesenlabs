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
