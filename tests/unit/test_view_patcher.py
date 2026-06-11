"""Unit: AnthropicViewPatcher (mocked client) — the NL refine EDIT path.

Mirrors test_spec_generator.py: happy patch, schema-reject-and-retry with the validator's errors
fed back, member-validation (unknown Cube member rejected), retry exhaustion -> raise (route 422),
graceful unconfigured raise, the lazy/import-safe client seam, and the callable contract that
SavedViews.refine_nl drives. The model is given the EXISTING spec + the instruction; it returns the
FULL patched spec, validated against the schema + catalog before the store ever persists it.
"""
import json

import pytest

from agents.roster import SONNET
from conv.view_patcher import (
    UNCONFIGURED_ERROR,
    AnthropicViewPatcher,
    ViewPatchError,
)
from shared.config import ENV_ANTHROPIC_API_KEY

ALLOWED = ["Deals.pipeline_value", "Deals.count", "Deals.stage"]


# --------------------------------------------------------------------------- fakes
class _Block:
    def __init__(self, text, type="text"):
        self.type = type
        self.text = text


class _Resp:
    def __init__(self, blocks):
        self.content = blocks


class _FakeMessages:
    def __init__(self, payloads, error=None):
        self._payloads = list(payloads)
        self._error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return _Resp([_Block(self._payloads.pop(0))])


class FakeAnthropic:
    """Stands in for anthropic.Anthropic — just the .messages.create surface we use."""

    def __init__(self, payloads=(), error=None):
        self.messages = _FakeMessages(payloads, error)


def _kpi_spec():
    """The EXISTING view-spec a refine edits (a single-KPI pipeline-value view)."""
    return {
        "view_id": "pipeline", "title": "Pipeline", "semantic_refs": ["Deals.pipeline_value"],
        "layout": [{"type": "kpi", "metric": "Deals.pipeline_value"}],
    }


def _patched_chart_spec():
    """A valid patched spec: the same view turned into a chart (still real members)."""
    return {
        "view_id": "pipeline", "title": "Pipeline over time",
        "semantic_refs": ["Deals.pipeline_value"],
        "layout": [{"type": "chart", "encoding": "vega-lite",
                    "query": {"measures": ["Deals.pipeline_value"]}}],
    }


def _unknown_member_patch():
    return {
        "view_id": "pipeline", "title": "Bad", "semantic_refs": ["Deals.count"],
        "layout": [{"type": "kpi", "metric": "Deals.not_a_member"}],
    }


def _schema_violating_patch():
    # 'iframe' is not a catalog block type and 'semantic_refs' is missing — pure schema violation.
    return {
        "view_id": "pipeline", "title": "Evil",
        "layout": [{"type": "iframe", "src": "https://evil.example"}],
    }


# --------------------------------------------------------------------------- happy path
@pytest.mark.unit
def test_patch_happy_path_valid_first_try():
    client = FakeAnthropic(payloads=[json.dumps(_patched_chart_spec())])
    patcher = AnthropicViewPatcher(client=client, allowed_members=ALLOWED)
    out = patcher(_kpi_spec(), "make it a line chart")
    assert out == _patched_chart_spec()
    (call,) = client.messages.calls
    assert call["model"] == SONNET
    assert call["max_tokens"] > 0
    assert "view_id" in call["system"]  # the JSON schema is embedded in the system prompt
    body = call["messages"][0]["content"]
    assert call["messages"][0]["role"] == "user"
    assert "make it a line chart" in body
    assert "Deals.pipeline_value" in body  # the existing spec's members are shown to the model
    for member in ALLOWED:
        assert member in body  # the allowed catalog is passed to the model


@pytest.mark.unit
def test_patch_is_callable_contract_for_refine_nl():
    # SavedViews.refine_nl calls patcher(spec, instruction) -> patched spec.
    patcher = AnthropicViewPatcher(client=FakeAnthropic(payloads=[json.dumps(_patched_chart_spec())]))
    result = patcher(_kpi_spec(), "make it a chart")
    assert callable(patcher)
    assert isinstance(result, dict)
    assert result["view_id"] == "pipeline"


# --------------------------------------------------------------------------- schema reject-and-retry
@pytest.mark.unit
def test_schema_reject_and_retry_feeds_validator_errors_back():
    client = FakeAnthropic(payloads=[
        json.dumps(_schema_violating_patch()),  # attempt 1: bad schema (iframe block)
        json.dumps(_patched_chart_spec()),      # attempt 2: corrected
    ])
    out = AnthropicViewPatcher(client=client, allowed_members=ALLOWED)(
        _kpi_spec(), "make it a chart"
    )
    assert out == _patched_chart_spec()
    first, second = client.messages.calls
    assert "failed validation" not in first["messages"][0]["content"]
    retry_body = second["messages"][0]["content"]
    assert "failed validation" in retry_body
    assert "schema invalid" in retry_body  # the validator's words went back to the model


@pytest.mark.unit
def test_schema_violation_exhausts_and_raises():
    bad = json.dumps(_schema_violating_patch())
    client = FakeAnthropic(payloads=[bad, bad])
    with pytest.raises(ViewPatchError) as ei:
        AnthropicViewPatcher(client=client, allowed_members=ALLOWED)(_kpi_spec(), "embed evil")
    assert "schema invalid" in str(ei.value)
    assert len(client.messages.calls) == 2  # one patch + exactly one retry


# --------------------------------------------------------------------------- member validation
@pytest.mark.unit
def test_unknown_member_patch_is_rejected_then_retry_succeeds():
    client = FakeAnthropic(payloads=[
        json.dumps(_unknown_member_patch()),   # references Deals.not_a_member
        json.dumps(_patched_chart_spec()),
    ])
    out = AnthropicViewPatcher(client=client, allowed_members=ALLOWED)(
        _kpi_spec(), "break it down"
    )
    assert out == _patched_chart_spec()
    retry_body = client.messages.calls[1]["messages"][0]["content"]
    assert "Deals.not_a_member" in retry_body  # validator's member error fed back


@pytest.mark.unit
def test_unknown_member_patch_exhausts_and_raises():
    bad = json.dumps(_unknown_member_patch())
    client = FakeAnthropic(payloads=[bad, bad])
    with pytest.raises(ViewPatchError) as ei:
        AnthropicViewPatcher(client=client, allowed_members=ALLOWED)(_kpi_spec(), "break it down")
    assert "Deals.not_a_member" in str(ei.value)


@pytest.mark.unit
def test_no_catalog_skips_member_check_but_schema_still_enforced():
    # allowed_members=None (the live pre-catalog API-image posture): an unknown member passes the
    # patcher (the store re-validates against live members), but a schema violation still raises.
    ok = AnthropicViewPatcher(client=FakeAnthropic(payloads=[json.dumps(_unknown_member_patch())]))(
        _kpi_spec(), "break it down"
    )
    assert ok == _unknown_member_patch()  # member check skipped without a catalog
    with pytest.raises(ViewPatchError):
        AnthropicViewPatcher(client=FakeAnthropic(payloads=[json.dumps(_schema_violating_patch())] * 2))(
            _kpi_spec(), "embed evil"
        )


@pytest.mark.unit
def test_existing_spec_members_are_folded_into_a_partial_catalog():
    # A catalog that does NOT list the existing spec's member must not spuriously reject an edit
    # that merely keeps that reference — the existing spec's members are unioned in.
    narrow_catalog = ["Deals.count"]  # does not include Deals.pipeline_value
    out = AnthropicViewPatcher(
        client=FakeAnthropic(payloads=[json.dumps(_patched_chart_spec())]),
        allowed_members=narrow_catalog,
    )(_kpi_spec(), "make it a chart")
    assert out == _patched_chart_spec()  # kept Deals.pipeline_value, not rejected


# --------------------------------------------------------------------------- resilience
@pytest.mark.unit
def test_non_json_output_counts_as_failed_attempt_then_retry_succeeds():
    client = FakeAnthropic(payloads=["Sure! Here's an idea...", json.dumps(_patched_chart_spec())])
    out = AnthropicViewPatcher(client=client, allowed_members=ALLOWED)(_kpi_spec(), "make it a chart")
    assert out == _patched_chart_spec()
    assert len(client.messages.calls) == 2


@pytest.mark.unit
def test_api_error_never_crashes_raises_view_patch_error():
    client = FakeAnthropic(error=RuntimeError("simulated API outage"))
    with pytest.raises(ViewPatchError) as ei:
        AnthropicViewPatcher(client=client, allowed_members=ALLOWED)(_kpi_spec(), "make it a chart")
    assert "model call failed" in str(ei.value)
    assert len(client.messages.calls) == 2  # retried then gave up


@pytest.mark.unit
def test_markdown_fenced_json_is_tolerated():
    fenced = "```json\n" + json.dumps(_patched_chart_spec()) + "\n```"
    out = AnthropicViewPatcher(client=FakeAnthropic(payloads=[fenced]), allowed_members=ALLOWED)(
        _kpi_spec(), "make it a chart"
    )
    assert out == _patched_chart_spec()


# --------------------------------------------------------------------------- seam hygiene
@pytest.mark.unit
def test_unconfigured_patcher_raises_view_patch_error(monkeypatch):
    monkeypatch.delenv(ENV_ANTHROPIC_API_KEY, raising=False)
    patcher = AnthropicViewPatcher()  # no client, no key
    assert patcher.configured is False
    with pytest.raises(ViewPatchError) as ei:
        patcher(_kpi_spec(), "make it a chart")
    assert str(ei.value) == UNCONFIGURED_ERROR


@pytest.mark.unit
def test_non_dict_spec_is_refused():
    patcher = AnthropicViewPatcher(client=FakeAnthropic(payloads=[json.dumps(_patched_chart_spec())]))
    with pytest.raises(ViewPatchError):
        patcher("not a spec", "make it a chart")  # type: ignore[arg-type]


@pytest.mark.unit
def test_client_is_lazy_and_construction_needs_no_network():
    patcher = AnthropicViewPatcher(api_key="unused")
    assert patcher._client is None  # nothing built until the first real call
    assert patcher.model == SONNET
    assert patcher.configured is True  # an explicit key counts as configured


# --------------------------------------------------------------------------- store wiring
@pytest.mark.unit
def test_refine_nl_drives_the_patcher_end_to_end():
    # The patcher drops straight into SavedViews.refine_nl: existing view -> patched -> versioned.
    from api.views import SavedViews

    sv = SavedViews(allowed_members=set(ALLOWED))
    sv.save("t1", _kpi_spec(), source_prompt="show pipeline", created_by="u1")
    patcher = AnthropicViewPatcher(
        client=FakeAnthropic(payloads=[json.dumps(_patched_chart_spec())]),
        allowed_members=ALLOWED,
    )
    row = sv.refine_nl("t1", "pipeline", "make it a line chart", patcher, created_by="u1")
    assert row["version"] == 2
    assert row["spec_json"]["layout"][0]["type"] == "chart"
