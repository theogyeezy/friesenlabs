"""Unit: build_view validates + reject-and-retries; never returns unvalidated output."""
import pytest

from agents.tools.base import ToolContext
from agents.tools.build_view import BuildView

ALLOWED = ["Deals.pipeline_value", "Deals.count", "Deals.stage"]


class FakeCube:
    def members(self, tenant_id):
        return list(ALLOWED)


def _good_spec():
    return {
        "view_id": "v1", "title": "Pipeline", "semantic_refs": ["Deals.count"],
        "layout": [{"type": "kpi", "metric": "Deals.pipeline_value"}],
    }


def _bad_spec():
    return {
        "view_id": "v1", "title": "Bad", "semantic_refs": ["Deals.count"],
        "layout": [{"type": "kpi", "metric": "Deals.not_a_member"}],
    }


def _ctx(generate):
    return ToolContext(tenant_id="t1", cube=FakeCube(), extra={"generate_spec": generate})


@pytest.mark.unit
def test_returns_valid_spec_first_try():
    def gen(**kw):
        return _good_spec()
    out = BuildView().invoke(_ctx(gen), request="show pipeline")
    assert out["result"]["status"] == "valid"
    assert out["result"]["attempts"] == 1


@pytest.mark.unit
def test_reject_and_retry_then_succeed():
    calls = {"n": 0}

    def gen(request, allowed_members, prev_error):
        calls["n"] += 1
        # First attempt emits an invalid spec; the error is fed back; second attempt fixes it.
        if calls["n"] == 1:
            assert prev_error is None
            return _bad_spec()
        assert prev_error is not None  # the validator's error was passed back
        return _good_spec()

    out = BuildView().invoke(_ctx(gen), request="show pipeline")
    assert out["result"]["status"] == "valid"
    assert out["result"]["attempts"] == 2


@pytest.mark.unit
def test_never_returns_invalid_after_max_attempts():
    def gen(**kw):
        return _bad_spec()  # always invalid
    out = BuildView().invoke(_ctx(gen), request="show pipeline")
    assert out["result"]["status"] == "invalid"
    assert "Deals.not_a_member" in out["result"]["error"]
