"""Unit: AnthropicSpecGenerator (mocked client) — happy path, reject-and-retry with validator
errors fed back, retry exhaustion, real schema validation, graceful unconfigured degradation,
and the lazy/import-safe client seam. Plus BuildView wiring for the generator-object contract.
"""
import json

import pytest

from agents.roster import SONNET
from agents.tools.base import ToolContext
from agents.tools.build_view import BuildView
from agents.tools.spec_generator import (
    UNCONFIGURED_ERROR,
    AnthropicSpecGenerator,
    _parse_spec,
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


class FakeCube:
    def members(self, tenant_id):
        return list(ALLOWED)


def _good_spec():
    return {
        "view_id": "pipeline", "title": "Pipeline", "semantic_refs": ["Deals.pipeline_value"],
        "layout": [{"type": "kpi", "metric": "Deals.pipeline_value"}],
    }


def _unknown_member_spec():
    return {
        "view_id": "pipeline", "title": "Bad", "semantic_refs": ["Deals.count"],
        "layout": [{"type": "kpi", "metric": "Deals.not_a_member"}],
    }


def _schema_violating_spec():
    # 'iframe' is not a catalog block type and 'semantic_refs' is missing — pure schema violation
    # (every member it could reference is real, so only validate_schema can reject it).
    return {
        "view_id": "evil", "title": "Evil",
        "layout": [{"type": "iframe", "src": "https://evil.example"}],
    }


# --------------------------------------------------------------------------- happy path
@pytest.mark.unit
def test_happy_path_valid_first_try():
    client = FakeAnthropic(payloads=[json.dumps(_good_spec())])
    gen = AnthropicSpecGenerator(client=client)
    out = gen.generate(request="show pipeline value", allowed_members=ALLOWED)
    assert out["valid"] is True
    assert out["spec"] == _good_spec()
    assert out["errors"] == []
    assert out["attempts"] == 1


@pytest.mark.unit
def test_model_call_shape_uses_repo_sonnet_and_carries_request_and_members():
    client = FakeAnthropic(payloads=[json.dumps(_good_spec())])
    AnthropicSpecGenerator(client=client).generate(
        request="show pipeline value", allowed_members=ALLOWED
    )
    (call,) = client.messages.calls
    assert call["model"] == SONNET
    assert call["max_tokens"] > 0
    assert "view_id" in call["system"]  # the JSON schema is embedded in the system prompt
    body = call["messages"][0]["content"]
    assert call["messages"][0]["role"] == "user"
    assert "show pipeline value" in body
    for member in ALLOWED:
        assert member in body


# --------------------------------------------------------------------------- reject-and-retry
@pytest.mark.unit
def test_invalid_spec_retries_once_with_validator_errors_fed_back():
    client = FakeAnthropic(
        payloads=[json.dumps(_unknown_member_spec()), json.dumps(_good_spec())]
    )
    out = AnthropicSpecGenerator(client=client).generate(
        request="show pipeline", allowed_members=ALLOWED
    )
    assert out["valid"] is True
    assert out["spec"] == _good_spec()
    assert out["attempts"] == 2
    assert any("Deals.not_a_member" in e for e in out["errors"])  # first attempt's failure kept
    # The retry prompt carried the validator's words back to the model.
    first, second = client.messages.calls
    assert "failed validation" not in first["messages"][0]["content"]
    retry_body = second["messages"][0]["content"]
    assert "failed validation" in retry_body
    assert "Deals.not_a_member" in retry_body


@pytest.mark.unit
def test_retry_exhausted_returns_invalid_with_errors_never_a_spec():
    bad = json.dumps(_unknown_member_spec())
    client = FakeAnthropic(payloads=[bad, bad])
    out = AnthropicSpecGenerator(client=client).generate(
        request="show pipeline", allowed_members=ALLOWED
    )
    assert out["valid"] is False
    assert out["spec"] is None
    assert out["attempts"] == 2
    assert len(client.messages.calls) == 2  # one generation + exactly one retry
    assert all("Deals.not_a_member" in e for e in out["errors"])


@pytest.mark.unit
def test_schema_validation_actually_runs_iframe_block_rejected():
    # A spec that violates the JSON schema itself (non-catalog block type, missing
    # semantic_refs) must be rejected even though it references no unknown member.
    bad = json.dumps(_schema_violating_spec())
    client = FakeAnthropic(payloads=[bad, bad])
    out = AnthropicSpecGenerator(client=client).generate(
        request="embed something", allowed_members=ALLOWED
    )
    assert out["valid"] is False
    assert out["spec"] is None
    assert any("schema invalid" in e for e in out["errors"])


@pytest.mark.unit
def test_non_json_output_counts_as_failed_attempt_then_retry_succeeds():
    client = FakeAnthropic(payloads=["Sure! Here's a dashboard idea...", json.dumps(_good_spec())])
    out = AnthropicSpecGenerator(client=client).generate(
        request="show pipeline", allowed_members=ALLOWED
    )
    assert out["valid"] is True
    assert out["attempts"] == 2
    assert any("not a JSON object" in e for e in out["errors"])


@pytest.mark.unit
def test_api_error_never_raises_returns_invalid():
    client = FakeAnthropic(error=RuntimeError("simulated API outage"))
    out = AnthropicSpecGenerator(client=client).generate(
        request="show pipeline", allowed_members=ALLOWED
    )
    assert out["valid"] is False
    assert out["spec"] is None
    assert any("model call failed" in e for e in out["errors"])


@pytest.mark.unit
def test_markdown_fenced_json_is_tolerated():
    fenced = "```json\n" + json.dumps(_good_spec()) + "\n```"
    client = FakeAnthropic(payloads=[fenced])
    out = AnthropicSpecGenerator(client=client).generate(
        request="show pipeline", allowed_members=ALLOWED
    )
    assert out["valid"] is True
    assert out["attempts"] == 1


@pytest.mark.unit
def test_json_wrapped_in_prose_is_recovered():
    wrapped = "Here you go:\n" + json.dumps(_good_spec())
    assert _parse_spec(wrapped) == _good_spec()
    assert _parse_spec("[1, 2, 3]") is None  # a non-dict is not a spec
    assert _parse_spec("") is None


# --------------------------------------------------------------------------- seam hygiene
@pytest.mark.unit
def test_unconfigured_generator_degrades_gracefully(monkeypatch):
    monkeypatch.delenv(ENV_ANTHROPIC_API_KEY, raising=False)
    gen = AnthropicSpecGenerator()  # no client, no key
    assert gen.configured is False
    out = gen.generate(request="show pipeline", allowed_members=ALLOWED)
    assert out == {"valid": False, "spec": None, "errors": [UNCONFIGURED_ERROR], "attempts": 0}


@pytest.mark.unit
def test_client_is_lazy_and_construction_needs_no_network():
    gen = AnthropicSpecGenerator(api_key="unused")
    assert gen._client is None  # nothing built until the first real call
    assert gen.model == SONNET  # the repo's Sonnet-tier model id
    assert gen.configured is True  # an explicit key counts as configured


# --------------------------------------------------------------------------- BuildView wiring
def _ctx(extra=None):
    return ToolContext(tenant_id="t1", cube=FakeCube(), extra=extra or {})


@pytest.mark.unit
def test_build_view_accepts_generator_via_ctx_extra():
    gen = AnthropicSpecGenerator(client=FakeAnthropic(payloads=[json.dumps(_good_spec())]))
    out = BuildView().invoke(_ctx({"generate_spec": gen}), request="show pipeline")
    assert out["result"]["status"] == "valid"
    assert out["result"]["spec"] == _good_spec()
    assert out["result"]["attempts"] == 1


@pytest.mark.unit
def test_build_view_accepts_injected_default_generator():
    gen = AnthropicSpecGenerator(client=FakeAnthropic(payloads=[json.dumps(_good_spec())]))
    out = BuildView(generator=gen).invoke(_ctx(), request="show pipeline")
    assert out["result"]["status"] == "valid"


@pytest.mark.unit
def test_build_view_ctx_extra_wins_over_injected_default():
    extra_gen = AnthropicSpecGenerator(client=FakeAnthropic(payloads=[json.dumps(_good_spec())]))
    default_gen = AnthropicSpecGenerator(client=FakeAnthropic(error=RuntimeError("not me")))
    out = BuildView(generator=default_gen).invoke(
        _ctx({"generate_spec": extra_gen}), request="show pipeline"
    )
    assert out["result"]["status"] == "valid"
    assert default_gen._client.messages.calls == []  # the default was never touched


@pytest.mark.unit
def test_build_view_unconfigured_generator_yields_invalid_not_crash(monkeypatch):
    monkeypatch.delenv(ENV_ANTHROPIC_API_KEY, raising=False)
    out = BuildView(generator=AnthropicSpecGenerator()).invoke(_ctx(), request="show pipeline")
    assert out["result"]["status"] == "invalid"
    assert "unconfigured" in out["result"]["error"]


@pytest.mark.unit
def test_build_view_revalidates_a_lying_generator():
    class LyingGenerator:
        def generate(self, *, request, allowed_members):
            return {"valid": True, "spec": _unknown_member_spec(), "errors": [], "attempts": 1}

    out = BuildView().invoke(_ctx({"generate_spec": LyingGenerator()}), request="show pipeline")
    assert out["result"]["status"] == "invalid"  # defense in depth: build_view re-validates
    assert "Deals.not_a_member" in out["result"]["error"]


@pytest.mark.unit
def test_build_view_without_any_generator_still_raises():
    with pytest.raises(RuntimeError, match="generate_spec"):
        BuildView().invoke(_ctx(), request="show pipeline")
