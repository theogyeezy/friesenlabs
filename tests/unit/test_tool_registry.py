"""Unit: the trusted tool registry — names resolve, and side_effecting/channel come from the tool."""
import pytest

from agents.roster import roster
from agents.tools.registry import TOOL_REGISTRY, resolve, tool_meta


@pytest.mark.unit
def test_every_roster_tool_name_resolves():
    names = {t for spec in roster() for t in spec.tools}
    for n in names:
        assert n in TOOL_REGISTRY, f"roster references unknown tool {n!r}"
        assert resolve(n).name == n


@pytest.mark.unit
def test_run_model_and_build_view_are_registered_and_rostered():
    assert "run_model" in TOOL_REGISTRY and "build_view" in TOOL_REGISTRY
    scout = next(s for s in roster() if s.name == "scout")
    assert "run_model" in scout.tools and "build_view" in scout.tools


@pytest.mark.unit
def test_tool_meta_is_server_truth():
    assert tool_meta("send_email") == {"side_effecting": True, "channel": "email"}
    assert tool_meta("update_deal")["side_effecting"] is True
    assert tool_meta("issue_quote")["side_effecting"] is True
    for ro in ("read_crm", "search_rag", "query_cube", "run_model", "build_view", "draft_email"):
        assert tool_meta(ro)["side_effecting"] is False


@pytest.mark.unit
def test_unknown_tool_raises():
    with pytest.raises(KeyError):
        tool_meta("definitely_not_a_tool")
