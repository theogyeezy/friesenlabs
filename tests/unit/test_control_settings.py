"""Unit: the persisted control plane — kill switch + autonomy dial over shared settings.

Offline proofs of the accountability semantics:
  * multi-instance kill switch: two PersistedKillSwitch facades (two "API tasks") over ONE
    shared store — a flip on instance A is seen by instance B once its TTL window rolls
    (and immediately with ttl=0), in BOTH scopes.
  * read-your-own-write: the flipping instance sees its flip immediately (cache invalidation).
  * the gate consults the persisted switch and the persisted autonomy level (level_provider).
  * the in-memory KillSwitch grew the same status/set surface the routes serve.
"""
import pytest

from api.control.autonomy import AutonomyConfig
from api.control.gate import ActionGate, GateContext
from api.control.killswitch import KillSwitch
from api.control.settings import (
    GLOBAL_CONTROL_TENANT,
    AutonomyDial,
    InMemoryControlSettings,
    PersistedAutonomyDial,
    PersistedKillSwitch,
)
from api.control.types import Action, Decision, Level


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class SpyExecutor:
    def __init__(self):
        self.calls = []

    def __call__(self, action):
        self.calls.append(action)
        return {"ok": True}


# --------------------------------------------------------------- multi-instance kill switch
@pytest.mark.unit
def test_flip_on_one_instance_visible_on_the_other_within_ttl():
    store = InMemoryControlSettings()  # the shared persistence (Pg in prod)
    clock = FakeClock()
    a = PersistedKillSwitch(store, ttl_seconds=2.0, clock=clock)
    b = PersistedKillSwitch(store, ttl_seconds=2.0, clock=clock)

    assert a.is_paused("t1") is False
    assert b.is_paused("t1") is False  # b's cache now holds the disengaged read

    a.set("t1", True)                  # instance A flips (write-through + local invalidation)
    assert a.is_paused("t1") is True   # read-your-own-write, immediately

    # Instance B still serves its cached read inside the TTL window...
    assert b.is_paused("t1") is False
    # ...and sees the flip once the TTL rolls — "within seconds", never never.
    clock.t += 2.1
    assert b.is_paused("t1") is True


@pytest.mark.unit
def test_global_flip_pauses_every_tenant_on_every_instance():
    store = InMemoryControlSettings()
    a = PersistedKillSwitch(store, ttl_seconds=0.0)  # ttl=0 -> every read hits the store
    b = PersistedKillSwitch(store, ttl_seconds=0.0)

    a.set("t-operator", True, scope="global")
    assert b.is_paused("t1") is True
    assert b.is_paused("t2") is True
    assert b.status("t1") == {"engaged": True, "scope": "global"}

    a.set("t-operator", False, scope="global")
    assert b.is_paused("t1") is False
    assert b.status("t1") == {"engaged": False, "scope": "tenant"}


@pytest.mark.unit
def test_tenant_scope_isolated_and_status_shape():
    store = InMemoryControlSettings()
    ks = PersistedKillSwitch(store, ttl_seconds=0.0)
    ks.set("tA", True)
    assert ks.is_paused("tA") is True
    assert ks.is_paused("tB") is False  # another tenant's flip never bleeds over
    assert ks.status("tA") == {"engaged": True, "scope": "tenant"}
    assert ks.status("tB") == {"engaged": False, "scope": "tenant"}
    # The global sentinel row is reserved — flipping tenant 'tA' never touched it.
    assert store.get(GLOBAL_CONTROL_TENANT) is None


@pytest.mark.unit
def test_bad_scope_rejected():
    ks = PersistedKillSwitch(InMemoryControlSettings(), ttl_seconds=0.0)
    with pytest.raises(ValueError):
        ks.set("t1", True, scope="everything")
    with pytest.raises(ValueError):
        KillSwitch().set("t1", True, scope="everything")


# --------------------------------------------------------------- the gate consults persistence
@pytest.mark.unit
def test_gate_blocks_when_persisted_switch_engaged_and_resumes_after_release():
    store = InMemoryControlSettings()
    ks = PersistedKillSwitch(store, ttl_seconds=0.0)
    ex = SpyExecutor()
    ctx = GateContext(tenant_id="t1",
                      autonomy_config=AutonomyConfig(default_level=Level.L3),
                      executor=ex, killswitch=ks)

    ks.set("t1", True)
    res = ActionGate().run(Action(name="read_crm", side_effecting=False), ctx)
    assert res.status == "blocked" and res.decision is Decision.BLOCK
    assert ex.calls == []

    ks.set("t1", False)
    res = ActionGate().run(Action(name="read_crm", side_effecting=False), ctx)
    assert res.status == "ok"
    assert len(ex.calls) == 1


@pytest.mark.unit
def test_gate_reads_persisted_autonomy_level_not_the_default():
    store = InMemoryControlSettings()
    dial = PersistedAutonomyDial(store, ttl_seconds=0.0)
    config = AutonomyConfig(level_provider=dial.provider)  # default stays L1
    ex = SpyExecutor()
    ctx = GateContext(tenant_id="t1", autonomy_config=config, executor=ex)
    side_effect = Action(name="update_deal", side_effecting=True, payload={"x": 1},
                         value_at_stake=10.0)

    # Unseeded -> the L1 default applies: side effect pends.
    res = ActionGate().run(side_effect, ctx)
    assert res.status == "pending_approval"
    assert ex.calls == []

    # Dial the PERSISTED level to L3 -> the same action now auto-executes.
    dial.set("t1", Level.L3)
    res = ActionGate().run(side_effect, ctx)
    assert res.status == "ok"
    assert len(ex.calls) == 1

    # Another tenant is untouched by t1's dial.
    ctx2 = GateContext(tenant_id="t2", autonomy_config=config, executor=ex)
    assert ActionGate().run(side_effect, ctx2).status == "pending_approval"


@pytest.mark.unit
def test_autonomy_dial_ttl_and_two_instances():
    store = InMemoryControlSettings()
    clock = FakeClock()
    a = PersistedAutonomyDial(store, ttl_seconds=2.0, clock=clock)
    b = PersistedAutonomyDial(store, ttl_seconds=2.0, clock=clock)

    assert b.get("t1") is Level.L1     # unseeded -> default (and now cached on b)
    a.set("t1", Level.L2)
    assert a.get("t1") is Level.L2     # read-your-own-write
    assert b.get("t1") is Level.L1     # b: stale inside the TTL window
    clock.t += 2.1
    assert b.get("t1") is Level.L2     # ...fresh after it


@pytest.mark.unit
def test_provider_tolerates_junk_level_in_store():
    store = InMemoryControlSettings()
    store._rows["t1"] = {"tenant_id": "t1", "autonomy_level": "L9",
                         "killswitch_engaged": False}
    dial = PersistedAutonomyDial(store, ttl_seconds=0.0)
    assert dial.provider("t1") is None   # junk never crashes the gate
    assert dial.get("t1") is Level.L1    # the default applies


@pytest.mark.unit
def test_explicit_override_beats_provider():
    dial = PersistedAutonomyDial(InMemoryControlSettings(), ttl_seconds=0.0)
    dial.set("t1", Level.L0)
    config = AutonomyConfig(overrides={"t1": Level.L3}, level_provider=dial.provider)
    from api.control.autonomy import resolve
    assert resolve(config, None, "t1") is Level.L3  # tests' explicit override stays supreme
    assert resolve(config, None, "t2") is Level.L1  # unseeded other tenant -> default


# --------------------------------------------------------------- in-memory surfaces
@pytest.mark.unit
def test_inmemory_killswitch_route_surface_matches_persisted():
    ks = KillSwitch()
    assert ks.status("t1") == {"engaged": False, "scope": "tenant"}
    ks.set("t1", True)
    assert ks.status("t1") == {"engaged": True, "scope": "tenant"}
    ks.set("anyone", True, scope="global")
    assert ks.status("t2") == {"engaged": True, "scope": "global"}
    ks.set("anyone", False, scope="global")
    ks.set("t1", False)
    assert ks.status("t1") == {"engaged": False, "scope": "tenant"}


@pytest.mark.unit
def test_inmemory_autonomy_dial_is_gate_visible():
    config = AutonomyConfig()
    dial = AutonomyDial(config)
    assert dial.get("t1") is Level.L1
    dial.set("t1", Level.L3)
    assert dial.get("t1") is Level.L3
    from api.control.autonomy import resolve
    assert resolve(config, None, "t1") is Level.L3  # the gate resolves the same overrides


@pytest.mark.unit
def test_inmemory_store_rejects_bad_level():
    with pytest.raises(ValueError):
        InMemoryControlSettings().set_autonomy("t1", "L9")
