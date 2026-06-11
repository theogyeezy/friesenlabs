"""Unit: playbook definitions are SPEC, NOT CODE — schema + owned-roster cross-checks.

Proves the two validation layers (agents/playbooks):
  * JSON schema (shared/schemas/playbook.schema.json): shape, closed enums,
    additionalProperties: false, and the DRAFT-ONLY constant — greenlight.side_effects only
    admits 'always_ask', so a playbook can never grant a send/CRM-write autonomy;
  * cross-checks: roster agents must exist in the owned roster, and tools must be a SUBSET of
    that agent's owned grant resolved through the trusted registry (no privilege escalation).

Also proves the committed starter library: all 5 templates validate, ids are unique, and
every template keeps the draft-only Greenlight policy.
"""
import copy

import pytest

from agents.playbooks import PlaybookValidationError, is_valid, validate
from agents.playbooks.templates import get_template, list_templates


def good_definition() -> dict:
    return {
        "name": "Test playbook",
        "description": "A valid definition.",
        "trigger": {"kind": "event", "event": "lead.created"},
        "roster": [
            {"agent": "scout", "tools": ["search_rag", "read_crm"]},
            {"agent": "nadia", "tools": ["draft_email"]},
        ],
        "autonomy": "L1",
        "greenlight": {"side_effects": "always_ask", "note": "test"},
    }


# --------------------------------------------------------------------------- #
# schema layer
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_valid_definition_passes():
    validate(good_definition())  # must not raise
    assert is_valid(good_definition())


@pytest.mark.unit
@pytest.mark.parametrize("missing", ["name", "trigger", "roster", "autonomy", "greenlight"])
def test_missing_required_field_fails(missing):
    d = good_definition()
    del d[missing]
    with pytest.raises(PlaybookValidationError) as e:
        validate(d)
    assert e.value.reason == "schema invalid"


@pytest.mark.unit
def test_non_object_definition_fails():
    with pytest.raises(PlaybookValidationError):
        validate(["not", "an", "object"])  # type: ignore[arg-type]


@pytest.mark.unit
def test_unknown_top_level_key_rejected():
    d = good_definition()
    d["code"] = "import os"  # spec-not-code: no executable payloads, no surprise keys
    with pytest.raises(PlaybookValidationError):
        validate(d)


@pytest.mark.unit
def test_bad_autonomy_level_fails():
    d = good_definition()
    d["autonomy"] = "L9"
    with pytest.raises(PlaybookValidationError):
        validate(d)


@pytest.mark.unit
@pytest.mark.parametrize("value", ["auto", "never_ask", "off", ""])
def test_side_effects_only_admits_always_ask(value):
    """THE DRAFT-ONLY GUARANTEE at the schema level: a playbook cannot grant side-effect
    autonomy — the only legal greenlight.side_effects value is 'always_ask'."""
    d = good_definition()
    d["greenlight"]["side_effects"] = value
    with pytest.raises(PlaybookValidationError):
        validate(d)


@pytest.mark.unit
def test_trigger_kind_is_a_closed_enum():
    d = good_definition()
    d["trigger"] = {"kind": "webhook"}
    with pytest.raises(PlaybookValidationError):
        validate(d)


@pytest.mark.unit
def test_schedule_trigger_requires_schedule():
    d = good_definition()
    d["trigger"] = {"kind": "schedule"}
    with pytest.raises(PlaybookValidationError):
        validate(d)
    d["trigger"] = {"kind": "schedule", "schedule": "0 13 * * 1"}
    validate(d)


@pytest.mark.unit
def test_event_trigger_requires_event():
    d = good_definition()
    d["trigger"] = {"kind": "event"}
    with pytest.raises(PlaybookValidationError):
        validate(d)


@pytest.mark.unit
def test_manual_trigger_needs_no_extras():
    d = good_definition()
    d["trigger"] = {"kind": "manual"}
    validate(d)


@pytest.mark.unit
def test_empty_roster_fails():
    d = good_definition()
    d["roster"] = []
    with pytest.raises(PlaybookValidationError):
        validate(d)


# --------------------------------------------------------------------------- #
# owned-roster cross-checks (the trusted source of truth)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_unknown_agent_fails():
    d = good_definition()
    d["roster"][0]["agent"] = "moriarty"
    with pytest.raises(PlaybookValidationError) as e:
        validate(d)
    assert e.value.reason == "unknown agent"


@pytest.mark.unit
def test_unknown_tool_fails():
    d = good_definition()
    d["roster"][0]["tools"] = ["rm_dash_rf"]
    with pytest.raises(PlaybookValidationError) as e:
        validate(d)
    assert e.value.reason == "unknown tool"


@pytest.mark.unit
def test_tool_escalation_fails():
    """No privilege escalation: send_email IS in the trusted registry, but it is NOT in
    scout's owned grant — a playbook can narrow an agent's tools, never widen them."""
    d = good_definition()
    d["roster"][0]["tools"] = ["send_email"]
    with pytest.raises(PlaybookValidationError) as e:
        validate(d)
    assert e.value.reason == "tool not granted"


@pytest.mark.unit
def test_omitted_tools_means_owned_grant():
    d = good_definition()
    d["roster"] = [{"agent": "pip"}]
    validate(d)


# --------------------------------------------------------------------------- #
# the committed starter library
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_five_templates_ship():
    templates = list_templates()
    assert len(templates) == 5
    ids = [t["template_id"] for t in templates]
    assert len(set(ids)) == 5, f"duplicate template ids: {ids}"
    assert set(ids) == {
        "lead-followup-drafter", "pipeline-hygiene-scout", "weekly-summary-reporter",
        "stale-deal-nudger", "data-quality-auditor",
    }


@pytest.mark.unit
def test_every_template_validates():
    for t in list_templates():
        validate(t["definition"])  # committed JSON must always be instantiable
        assert t.get("summary"), f"{t['template_id']} has no summary"


@pytest.mark.unit
def test_every_template_is_draft_only():
    for t in list_templates():
        assert t["definition"]["greenlight"]["side_effects"] == "always_ask"


@pytest.mark.unit
def test_get_template_returns_a_copy():
    a = get_template("stale-deal-nudger")
    a["definition"]["name"] = "mutated"
    b = get_template("stale-deal-nudger")
    assert b["definition"]["name"] == "Stale-deal nudger", "templates must be served as copies"


@pytest.mark.unit
def test_get_template_unknown_is_none():
    assert get_template("not-a-template") is None


@pytest.mark.unit
def test_list_templates_returns_copies():
    copy.deepcopy(list_templates())[0]["definition"]["roster"].clear()
    assert list_templates()[0]["definition"]["roster"], "list_templates leaked internal state"
