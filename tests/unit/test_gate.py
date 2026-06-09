"""Unit: the action gate enforces propose->validate->autonomy->Greenlight->execute->trace."""
import pytest

from api.control.autonomy import AutonomyConfig
from api.control.gate import ActionGate, GateContext
from api.control.greenlight import Greenlight
from api.control.killswitch import KillSwitch
from api.control.traces import InMemoryTraceStore
from api.control.types import Action, Decision, Level


class SpyExecutor:
    def __init__(self):
        self.calls = []

    def __call__(self, action):
        self.calls.append(action)
        return {"sent": True}


def _ctx(level=Level.L1, **kw):
    return GateContext(
        tenant_id="t1",
        autonomy_config=AutonomyConfig(default_level=level),
        executor=kw.pop("executor"),
        greenlight=kw.pop("greenlight", Greenlight()),
        killswitch=kw.pop("killswitch", KillSwitch()),
        trace_store=kw.pop("trace_store", InMemoryTraceStore()),
        compliance_critic=kw.pop("compliance_critic", None),
    )


COMPLIANT_EMAIL = dict(channel="email", payload={"body": "hi unsubscribe"})


@pytest.mark.unit
def test_readonly_executes_and_traces_once():
    ex = SpyExecutor()
    ts = InMemoryTraceStore()
    ctx = _ctx(executor=ex, trace_store=ts)
    res = ActionGate().run(Action(name="read_crm", side_effecting=False), ctx)
    assert res.status == "ok" and res.decision is Decision.AUTO
    assert len(ex.calls) == 1
    assert len(ts.rows) == 1


@pytest.mark.unit
def test_l1_side_effect_pends_and_does_not_execute():
    ex = SpyExecutor()
    ts = InMemoryTraceStore()
    ctx = _ctx(level=Level.L1, executor=ex, trace_store=ts)
    res = ActionGate().run(Action(name="send_email", side_effecting=True, **COMPLIANT_EMAIL), ctx)
    assert res.status == "pending_approval"
    assert ex.calls == []                 # never executed
    assert res.approval["status"] == "pending"
    assert len(ts.rows) == 1              # exactly one trace


@pytest.mark.unit
def test_compliance_hardfail_never_reaches_greenlight():
    ex = SpyExecutor()
    gl = Greenlight()
    ctx = _ctx(level=Level.L1, executor=ex, greenlight=gl)
    # email with no unsubscribe -> CAN-SPAM block
    bad = Action(name="send_email", side_effecting=True, channel="email", payload={"body": "buy now"})
    res = ActionGate().run(bad, ctx)
    assert res.status == "blocked" and res.decision is Decision.BLOCK
    assert ex.calls == []
    assert gl.list_pending("t1") == []    # nothing queued


@pytest.mark.unit
def test_l2_autoexecutes_under_threshold():
    ex = SpyExecutor()
    ctx = _ctx(level=Level.L2, executor=ex)
    action = Action(name="issue_quote", side_effecting=True, value_at_stake=100.0)
    res = ActionGate().run(action, ctx)
    assert res.status == "ok"
    assert len(ex.calls) == 1


@pytest.mark.unit
def test_every_path_writes_exactly_one_trace():
    for level, action in [
        (Level.L1, Action(name="send_email", side_effecting=True, **COMPLIANT_EMAIL)),
        (Level.L3, Action(name="send_email", side_effecting=True, **COMPLIANT_EMAIL)),
        (Level.L1, Action(name="read_crm", side_effecting=False)),
    ]:
        ts = InMemoryTraceStore()
        ctx = _ctx(level=level, executor=SpyExecutor(), trace_store=ts)
        ActionGate().run(action, ctx)
        assert len(ts.rows) == 1
