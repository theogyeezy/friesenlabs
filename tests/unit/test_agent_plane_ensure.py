"""Unit: signup/agent_plane.AgentPlaneEnsure — the EAGER real agent-plane glue (ratified #123,
docs/decisions/agent-plane-ensure-eager-vs-lazy.md) + its api/prod_deps wiring gate.

Proves the brief's contract on a MOCKED runtime (never live Anthropic):
  * first ensure() EAGER-creates the 7 roster specialists + coordinator IN THE EXISTING
    environment (never create_environment) and returns the ids provisioning persists;
  * second ensure() (workspace-store row present) is a NO-OP returning the stored ids —
    the brief's done-when criterion;
  * a 'stub-' placeholder row (the offline _Noop plane's leftovers) is NOT a store hit —
    it gets re-provisioned for real;
  * a partial failure RAISES -> the Provisioner parks the account (provisioning_failed),
    and the operator retry rebuilds the full roster and persists real ids;
  * the prod_deps gate: real ONLY under SIGNUP_REAL_DEPS + ANTHROPIC_API_KEY + UPLIFT_ENV_ID
    + a workspace store — every other combination keeps the _Noop stub-id fallback
    (deploy invariance: the live API task already carries the two AI-plane envs).
"""
import pytest

import api.prod_deps as prod_deps
from agents.coordinator import COORDINATOR
from agents.roster import roster
from agents.runtime import FakeRuntime, ManagedAgentsRuntime
from agents.workspace_store import InMemoryWorkspaceStore
from shared.config import ENV_ANTHROPIC_API_KEY, ENV_UPLIFT_ENV_ID, Config
from signup.agent_plane import AgentPlaneEnsure

ENV_ID = "env_existing_live"


def _plane(store=None, runtime_factory=None, **kw):
    return AgentPlaneEnsure(
        api_key=kw.pop("api_key", "sk-ant-org"),
        environment_id=kw.pop("environment_id", ENV_ID),
        workspace_store=store if store is not None else InMemoryWorkspaceStore(),
        runtime_factory=runtime_factory,
        **kw,
    )


def _refusing_factory():
    def f():
        raise AssertionError("no roster build expected — ensure() must no-op on the store hit")
    return f


# --------------------------------------------------------------- eager create (first call)
@pytest.mark.unit
def test_first_ensure_creates_full_roster_in_the_existing_environment():
    fake = FakeRuntime()
    plane = _plane(runtime_factory=lambda: fake)
    out = plane.ensure(tenant_id="t1", workspace_id="ws_1")

    # The provisioning-step contract: the three ids, environment = the EXISTING one.
    assert out["workspace_id"] == "ws_1"
    assert out["environment_id"] == ENV_ID
    assert out["coordinator_id"] in fake.coordinators

    # EAGER per the brief: all 7 specialists + the coordinator exist as persisted config…
    specialist_names = {s.name for s in roster()}
    created = {spec.name for aid, spec in fake.agents.items() if aid not in fake.coordinators}
    assert created == specialist_names and len(specialist_names) == 7
    assert fake.agents[out["coordinator_id"]].name == COORDINATOR.name
    # …the coordinator's roster snapshot is exactly those 7 agents…
    assert sorted(fake.coordinators[out["coordinator_id"]]) == sorted(
        aid for aid in fake.agents if aid not in fake.coordinators
    )
    # …and NO environment was created (the existing UPLIFT_ENV_ID one is used as-is).
    assert fake.environments == []


@pytest.mark.unit
def test_default_runtime_is_a_fresh_managed_runtime_bound_to_the_env():
    plane = _plane()  # no factory injected -> the prod default
    rt = plane._default_runtime()
    assert isinstance(rt, ManagedAgentsRuntime)
    assert rt._environment_id == ENV_ID
    # Bound runtimes REFUSE create_environment — ensure() can never mint a second env.
    with pytest.raises(RuntimeError, match="already bound"):
        rt.create_environment("uplift-vpc")
    # Fresh per call (never a shared instance accumulating cross-tenant session/coord state).
    assert plane._default_runtime() is not rt


@pytest.mark.unit
def test_constructor_refuses_missing_key_env_or_store():
    with pytest.raises(ValueError):
        AgentPlaneEnsure(api_key="", environment_id=ENV_ID,
                         workspace_store=InMemoryWorkspaceStore())
    with pytest.raises(ValueError):
        AgentPlaneEnsure(api_key="k", environment_id="",
                         workspace_store=InMemoryWorkspaceStore())
    with pytest.raises(ValueError):
        AgentPlaneEnsure(api_key="k", environment_id=ENV_ID, workspace_store=None)


# --------------------------------------------------------------- idempotency (second call)
@pytest.mark.unit
def test_second_ensure_is_a_noop_returning_the_stored_ids():
    store = InMemoryWorkspaceStore()
    fake = FakeRuntime()
    first = _plane(store=store, runtime_factory=lambda: fake).ensure(
        tenant_id="t1", workspace_id="ws_1")
    # Provisioning persists AFTER ensure (signup/provisioning._step_agent_plane).
    store.upsert("t1", first["workspace_id"], first["environment_id"], first["coordinator_id"])

    again = _plane(store=store, runtime_factory=_refusing_factory()).ensure(
        tenant_id="t1", workspace_id="ws_1")
    assert again == first  # no second roster, same ids back


@pytest.mark.unit
def test_store_hit_with_null_workspace_id_falls_back_to_the_callers():
    # A row persisted before the Anthropic-admin seam lands can hold workspace_id=None; the
    # no-op return must not clobber the freshly re-resolved workspace id with None.
    store = InMemoryWorkspaceStore()
    store.upsert("t1", None, "env_live", "coord_live")
    out = _plane(store=store, runtime_factory=_refusing_factory()).ensure(
        tenant_id="t1", workspace_id="ws_resolved")
    assert out == {"workspace_id": "ws_resolved", "environment_id": "env_live",
                   "coordinator_id": "coord_live"}


@pytest.mark.unit
def test_stub_row_is_reprovisioned_for_real_not_treated_as_a_hit():
    # The offline _Noop plane persisted placeholder ids; once the real plane is live the next
    # provisioning pass must build a REAL roster (the upsert then overwrites the stubs).
    store = InMemoryWorkspaceStore()
    store.upsert("t1", "stub-ws", "stub-env", "stub-coord")
    fake = FakeRuntime()
    out = _plane(store=store, runtime_factory=lambda: fake).ensure(
        tenant_id="t1", workspace_id="ws_real")
    assert out["environment_id"] == ENV_ID
    assert out["coordinator_id"] in fake.coordinators
    assert not any(str(v).startswith("stub-") for v in out.values())


@pytest.mark.unit
def test_incomplete_row_is_not_a_hit():
    store = InMemoryWorkspaceStore()
    store.upsert("t1", "ws_1", "env_live", None)   # no coordinator -> not provisioned
    fake = FakeRuntime()
    out = _plane(store=store, runtime_factory=lambda: fake).ensure(tenant_id="t1")
    assert out["coordinator_id"] in fake.coordinators


# --------------------------------------------------------------- partial failure -> park -> retry
class FlakyRuntime:
    """FakeRuntime that fails the Nth create_agent exactly once (the partial-roster outage)."""

    def __init__(self, fail_on_call=4):
        self.inner = FakeRuntime()
        self.fail_on_call = fail_on_call
        self.calls = 0
        self.failed_once = False

    def create_agent(self, spec):
        self.calls += 1
        if self.calls == self.fail_on_call and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("Anthropic 529: overloaded")
        return self.inner.create_agent(spec)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class _Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def f(*a, **k):
            self.calls.append((name, a, k))
        return f


class _Admin:
    def ensure_workspace(self, tenant_id):
        return f"ws-{tenant_id}"

    def create_workspace_key(self, ws_id, tenant_id):
        return "key"

    def set_limits(self, ws_id, tenant_id):
        pass


class _Secrets:
    def __init__(self):
        self.kv = {}

    def exists(self, k):
        return k in self.kv

    def put(self, k, v):
        self.kv[k] = v


class _Store:
    def __init__(self):
        self.rows = {}

    def get(self, aid):
        return self.rows.get(aid)

    def insert(self, acct):
        self.rows[acct.id] = acct

    def update(self, acct):
        self.rows[acct.id] = acct


def _paid_account(store):
    from signup.accounts import Account, State

    acct = Account(id="a1", email="u@x.com", phone="+15555550100", cognito_sub="sub1",
                   email_verified=True, phone_verified=True, state=State.PAID)
    store.insert(acct)
    return acct


@pytest.mark.unit
def test_partial_failure_parks_the_account_and_retry_rebuilds_the_roster():
    from signup.accounts import State
    from signup.provisioning import Provisioner

    flaky = FlakyRuntime(fail_on_call=4)     # dies mid-roster on the first pass only
    ws_store = InMemoryWorkspaceStore()
    store = _Store()
    acct = _paid_account(store)
    prov = Provisioner(
        store=store, mint_tenant_id=lambda aid: f"tenant-{aid}", db=_Recorder(),
        anthropic_admin=_Admin(), secrets=_Secrets(), cognito=_Recorder(),
        cube=_Recorder(), resend=_Recorder(),
        agent_plane=_plane(store=ws_store, runtime_factory=lambda: flaky),
        workspace_store=ws_store,
    )

    # Specialist #4 raises -> step 3 fails -> the account parks; NOTHING was persisted, so the
    # partial roster is an orphan the retry never resumes into (free per the brief's pricing).
    res = prov.provision(acct)
    assert res.ok is False and res.failed_step == "agent_plane"
    assert store.get("a1").state is State.PROVISIONING_FAILED
    assert ws_store.get("tenant-a1") is None

    # The operator/tenant retry re-runs the idempotent pipeline: a full fresh roster, persisted.
    out = prov.retry(store.get("a1"))
    assert out["status"] == "ok"
    assert store.get("a1").state is State.ACTIVE
    row = ws_store.get("tenant-a1")
    assert row["environment_id"] == ENV_ID
    assert row["coordinator_id"] in flaky.inner.coordinators
    assert row["workspace_id"] == "ws-tenant-a1"
    # 7 specialists from the retry + the 3 pre-failure orphans; exactly ONE coordinator.
    assert len(flaky.inner.coordinators) == 1

    # And a SECOND provisioning pass (re-delivered webhook shape) is a store-hit no-op.
    n_agents = len(flaky.inner.agents)
    acct2 = store.get("a1")
    acct2.state = State.PAID                 # force a re-run; ensure() must still no-op
    prov.provision(acct2)
    assert len(flaky.inner.agents) == n_agents
    assert ws_store.get("tenant-a1") == row


# --------------------------------------------------------------- the prod_deps wiring gate
def _cfg(monkeypatch, dsn=None, **overrides):
    monkeypatch.setattr(prod_deps, "load", lambda: Config(**overrides))
    monkeypatch.setattr(prod_deps, "dsn_from_env", lambda: dsn)


def _ai_plane_env(monkeypatch, api_key="sk-ant-org", env_id="env_live"):
    if api_key is None:
        monkeypatch.delenv(ENV_ANTHROPIC_API_KEY, raising=False)
    else:
        monkeypatch.setenv(ENV_ANTHROPIC_API_KEY, api_key)
    if env_id is None:
        monkeypatch.delenv(ENV_UPLIFT_ENV_ID, raising=False)
    else:
        monkeypatch.setenv(ENV_UPLIFT_ENV_ID, env_id)


@pytest.mark.unit
def test_master_switch_off_keeps_the_noop_despite_full_ai_plane_env(monkeypatch):
    """Deploy invariance: the live API task ALREADY carries ANTHROPIC_API_KEY + UPLIFT_ENV_ID —
    without the deliberate SIGNUP_REAL_DEPS flip a mere image deploy must keep the stub plane."""
    _ai_plane_env(monkeypatch)
    _cfg(monkeypatch)  # signup_real_deps deliberately ABSENT
    prov = prod_deps.build_provisioner(workspace_store=InMemoryWorkspaceStore())
    assert isinstance(prov.agent_plane, prod_deps._Noop)


@pytest.mark.unit
def test_gate_requires_both_ai_plane_envs(monkeypatch):
    _cfg(monkeypatch, signup_real_deps=True)
    store = InMemoryWorkspaceStore()

    _ai_plane_env(monkeypatch, api_key="sk-ant-org", env_id=None)
    assert isinstance(prod_deps.build_provisioner(workspace_store=store).agent_plane,
                      prod_deps._Noop)
    _ai_plane_env(monkeypatch, api_key=None, env_id="env_live")
    assert isinstance(prod_deps.build_provisioner(workspace_store=store).agent_plane,
                      prod_deps._Noop)

    _ai_plane_env(monkeypatch)  # both present -> the real eager plane
    plane = prod_deps.build_provisioner(workspace_store=store).agent_plane
    assert isinstance(plane, AgentPlaneEnsure)
    assert plane._environment_id == "env_live"
    assert plane._store is store


@pytest.mark.unit
def test_gate_requires_a_workspace_store(monkeypatch):
    # Switch + both envs but nowhere to persist/check ids (no store passed, no DSN): never
    # create live resources whose ids would be unreachable orphans.
    _ai_plane_env(monkeypatch)
    _cfg(monkeypatch, signup_real_deps=True)
    prov = prod_deps.build_provisioner()
    assert prov.workspace_store is None
    assert isinstance(prov.agent_plane, prod_deps._Noop)


@pytest.mark.unit
def test_lambda_bare_cold_start_defaults_the_workspace_store_from_the_dsn(monkeypatch):
    """The SFN path: build_provisioner() bare + switch + DSN -> a PgWorkspaceStore is built, so
    step 3 persists the SAME tenant_workspaces row the API task's in-process path does."""
    import psycopg2.pool

    class _FakePool:
        def getconn(self):
            raise AssertionError("no DB access expected at construction")

        def putconn(self, conn):
            pass

    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool",
                        lambda minc, maxc, dsn: _FakePool())
    _ai_plane_env(monkeypatch)
    _cfg(monkeypatch, dsn="postgresql://crm_app:x@db.example/uplift", signup_real_deps=True)
    prov = prod_deps.build_provisioner()
    from agents.workspace_store import PgWorkspaceStore

    assert isinstance(prov.workspace_store, PgWorkspaceStore)
    assert isinstance(prov.agent_plane, AgentPlaneEnsure)
    assert prov.agent_plane._store is prov.workspace_store


@pytest.mark.unit
def test_build_signup_deps_wires_the_real_plane_under_the_full_gate(monkeypatch):
    _ai_plane_env(monkeypatch)
    _cfg(monkeypatch, signup_real_deps=True)
    store = InMemoryWorkspaceStore()
    deps = prod_deps.build_signup_deps(workspace_store=store)
    provisioner = deps.payment.on_paid.__self__
    assert isinstance(provisioner.agent_plane, AgentPlaneEnsure)
    assert provisioner.workspace_store is store


@pytest.mark.unit
def test_noop_fallback_still_returns_stub_ids(monkeypatch):
    # The stub contract the conversation factory's guard keys off must never drift.
    assert prod_deps._Noop().ensure(tenant_id="t", workspace_id="ws") == {
        "workspace_id": "stub-ws", "environment_id": "stub-env",
        "coordinator_id": "stub-coord",
    }
