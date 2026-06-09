"""Unit: roster definitions are valid and within the hard multi-agent limits."""
import pytest

from agents import coordinator
from agents.roster import HAIKU, OPUS, SONNET, VALID_MODELS, roster
from agents.runtime import MAX_AGENTS_PER_ROSTER


@pytest.mark.unit
def test_roster_models_and_tools_valid():
    specs = roster()
    assert len(specs) == 7
    for s in specs:
        assert s.model in VALID_MODELS, s.name
        assert isinstance(s.tools, list)


@pytest.mark.unit
def test_model_tiering():
    by_name = {s.name: s for s in roster()}
    assert by_name["scout"].model == HAIKU
    assert by_name["nadia"].model == SONNET
    assert by_name["critic"].model == OPUS


@pytest.mark.unit
def test_roster_within_limit():
    assert len(roster()) <= MAX_AGENTS_PER_ROSTER


@pytest.mark.unit
def test_coordinator_is_opus():
    assert coordinator.COORDINATOR.model == OPUS
