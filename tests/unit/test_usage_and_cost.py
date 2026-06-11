"""Unit: usage counters, cost estimation/attribution, plan-tier config, and the cost seam.

No DB / network. Proves the math + the per-tenant isolation of the in-memory stores, the cost
estimate against shared/cost.py TIER_PRICES, the plan-tier resolution + env overrides, and that
Conversation attributes a turn's observed token usage to the right tenant exactly once.
"""
import pytest

from api.usage import (
    InMemoryCostRecorder,
    InMemoryUsageStore,
    current_period,
    estimate_cost,
)
from shared.config import (
    monthly_quota,
    normalize_plan,
    quota_enforcement,
    rate_limit_per_minute,
    tenant_limits_enabled,
)


# --------------------------------------------------------------------------- usage counter
@pytest.mark.unit
def test_usage_bump_returns_running_monthly_total():
    s = InMemoryUsageStore()
    assert s.bump("t1", "messages") == 1
    assert s.bump("t1", "messages") == 2
    assert s.bump("t1", "agent_actions") == 3  # both metrics roll into the running total
    cur = s.current("t1")
    assert cur["total"] == 3
    assert cur["by_metric"] == {"messages": 2, "agent_actions": 1}
    assert cur["period"] == current_period()


@pytest.mark.unit
def test_usage_counter_is_per_tenant():
    s = InMemoryUsageStore()
    s.bump("t1", "messages", amount=5)
    s.bump("t2", "messages", amount=2)
    assert s.current("t1")["total"] == 5
    assert s.current("t2")["total"] == 2


@pytest.mark.unit
def test_unknown_metric_rejected():
    s = InMemoryUsageStore()
    with pytest.raises(ValueError):
        s.bump("t1", "widgets")


# --------------------------------------------------------------------------- cost estimate
@pytest.mark.unit
@pytest.mark.parametrize("model,expected", [
    ("claude-haiku-4", 6.0),     # 1.00 in + 5.00 out per Mtok
    ("claude-sonnet-4", 18.0),   # 3.00 + 15.00
    ("claude-opus-4", 30.0),     # 5.00 + 25.00
    ("some-unknown-model", 18.0),  # defaults to sonnet (mid tier) — never silently $0
    (None, 18.0),
])
def test_estimate_cost_maps_model_to_tier(model, expected):
    assert estimate_cost(model, 1_000_000, 1_000_000) == expected


@pytest.mark.unit
def test_estimate_cost_clamps_negative_tokens():
    assert estimate_cost("claude-haiku-4", -5, -5) == 0.0


# --------------------------------------------------------------------------- cost recorder
@pytest.mark.unit
def test_cost_recorder_sums_per_tenant():
    c = InMemoryCostRecorder()
    c.record("t1", model="claude-haiku-4", in_tok=1_000_000, out_tok=0)   # $1.00
    c.record("t1", model="claude-haiku-4", in_tok=0, out_tok=1_000_000)   # $5.00
    c.record("t2", model="claude-opus-4", in_tok=1_000_000, out_tok=0)    # $5.00 — other tenant
    s1 = c.summary("t1")
    assert s1["events"] == 2
    assert s1["in_tok"] == 1_000_000 and s1["out_tok"] == 1_000_000
    assert s1["est_cost"] == 6.0
    assert c.summary("t2")["est_cost"] == 5.0   # isolated from t1


# --------------------------------------------------------------------------- plan config
@pytest.mark.unit
def test_normalize_plan_defaults_unknown_to_generous():
    assert normalize_plan("starter") == "starter"
    assert normalize_plan("TEAM") == "team"
    assert normalize_plan(None) == "scale"      # most generous fallback
    assert normalize_plan("enterprise") == "scale"


@pytest.mark.unit
def test_rate_limit_defaults_and_override(monkeypatch):
    assert rate_limit_per_minute("starter") == 120
    assert rate_limit_per_minute("scale") == 3000
    monkeypatch.setenv("RATE_LIMIT_STARTER_PER_MINUTE", "42")
    assert rate_limit_per_minute("starter") == 42
    monkeypatch.setenv("RATE_LIMIT_STARTER_PER_MINUTE", "junk")  # junk -> default
    assert rate_limit_per_minute("starter") == 120
    monkeypatch.setenv("RATE_LIMIT_STARTER_PER_MINUTE", "0")     # clamps to >= 1
    assert rate_limit_per_minute("starter") == 1


@pytest.mark.unit
def test_monthly_quota_defaults_override_and_unlimited(monkeypatch):
    assert monthly_quota("team") == 50_000
    monkeypatch.setenv("QUOTA_TEAM_MONTHLY", "9")
    assert monthly_quota("team") == 9
    monkeypatch.setenv("QUOTA_TEAM_MONTHLY", "0")   # <= 0 -> unlimited (None)
    assert monthly_quota("team") is None


@pytest.mark.unit
def test_quota_enforcement_fails_closed(monkeypatch):
    assert quota_enforcement() == "block"
    monkeypatch.setenv("QUOTA_ENFORCEMENT", "warn")
    assert quota_enforcement() == "warn"
    monkeypatch.setenv("QUOTA_ENFORCEMENT", "nonsense")  # anything but 'warn' -> block
    assert quota_enforcement() == "block"


@pytest.mark.unit
def test_tenant_limits_enabled_default_on(monkeypatch):
    assert tenant_limits_enabled() is True
    monkeypatch.setenv("TENANT_LIMITS_DISABLED", "true")
    assert tenant_limits_enabled() is False
    monkeypatch.setenv("TENANT_LIMITS_DISABLED", "0")   # only 'true'/'1' disables
    assert tenant_limits_enabled() is True


# --------------------------------------------------------------------------- conv cost seam
class _FakeRuntimeWithUsage:
    """A minimal runtime whose digest carries a usage block (the MA-shaped seam)."""

    def __init__(self, usage):
        self._usage = usage
        self.sent = []

    def create_session(self, coordinator_id, tenant_id, vault_id=None, environment_id=None):
        from agents.runtime import Session
        return Session(id="s1", tenant_id=tenant_id, coordinator_id=coordinator_id)

    def send_message(self, session, message):
        self.sent.append(message)
        return {"session_id": session.id, "tenant_id": session.tenant_id,
                "delegations": [], "answer": "ok", "pending_approvals": [], "tool_results": [],
                "usage": self._usage}


def _conversation(usage, recorder, tenant="tenant-A"):
    from datetime import date

    from conv.session import Conversation
    return Conversation(
        tenant_id=tenant, today=date(2026, 6, 11),
        runtime=_FakeRuntimeWithUsage(usage), coordinator_id="coord-1",
        environment_id="env-1", cost_recorder=recorder,
    )


@pytest.mark.unit
def test_conversation_attributes_token_usage_to_tenant():
    recorder = InMemoryCostRecorder()
    convo = _conversation(
        {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "model": "claude-haiku-4"},
        recorder, tenant="tenant-A",
    )
    convo.send("how are deals trending?")
    s = recorder.summary("tenant-A")
    assert s["events"] == 1
    assert s["in_tok"] == 1_000_000 and s["out_tok"] == 1_000_000
    assert s["est_cost"] == 6.0


@pytest.mark.unit
def test_conversation_skips_zero_usage_turn():
    recorder = InMemoryCostRecorder()
    convo = _conversation({"input_tokens": 0, "output_tokens": 0, "model": None}, recorder)
    convo.send("hello")
    assert recorder.summary("tenant-A")["events"] == 0   # nothing to attribute


@pytest.mark.unit
def test_conversation_with_no_recorder_does_not_raise():
    convo = _conversation({"input_tokens": 10, "output_tokens": 5, "model": "x"}, recorder=None)
    convo.send("hi")  # no recorder -> no-op, never raises
