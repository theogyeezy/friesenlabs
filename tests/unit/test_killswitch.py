"""Unit: the kill switch halts execution (tenant + global)."""
import pytest

from api.control.autonomy import AutonomyConfig
from api.control.gate import ActionGate, GateContext
from api.control.killswitch import KillSwitch
from api.control.types import Action, Decision, Level


class SpyExecutor:
    def __init__(self):
        self.calls = []

    def __call__(self, action):
        self.calls.append(action)
        return {"ok": True}


def _ctx(ks, ex):
    return GateContext(tenant_id="t1", autonomy_config=AutonomyConfig(default_level=Level.L3),
                       executor=ex, killswitch=ks)


@pytest.mark.unit
def test_tenant_pause_blocks_autoexecute():
    ks = KillSwitch(); ks.pause_tenant("t1")
    ex = SpyExecutor()
    res = ActionGate().run(Action(name="read_crm", side_effecting=False), _ctx(ks, ex))
    assert res.status == "blocked" and res.decision is Decision.BLOCK
    assert ex.calls == []


@pytest.mark.unit
def test_global_pause_blocks_all_tenants():
    ks = KillSwitch(); ks.pause_global()
    ex = SpyExecutor()
    res = ActionGate().run(Action(name="read_crm"), _ctx(ks, ex))
    assert res.status == "blocked"
    assert ex.calls == []


@pytest.mark.unit
def test_resume_restores_flow():
    ks = KillSwitch(); ks.pause_tenant("t1"); ks.resume_tenant("t1")
    ex = SpyExecutor()
    res = ActionGate().run(Action(name="read_crm", side_effecting=False), _ctx(ks, ex))
    assert res.status == "ok"
    assert len(ex.calls) == 1


@pytest.mark.unit
def test_other_tenant_not_affected():
    ks = KillSwitch(); ks.pause_tenant("t1")
    assert ks.is_paused("t1") is True
    assert ks.is_paused("t2") is False
