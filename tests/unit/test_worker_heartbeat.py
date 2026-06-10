"""Unit: the explicit workers_polling heartbeat + real-kwargs EnvironmentWorker wiring
(docs/decisions/workers-polling-heartbeat-assumption.md, RATIFIED #123).

Covers the brief's two verified bugs:
1. the heartbeat must be an explicit interval task (the SDK tools callable fires once per CLAIMED
   SESSION, never per poll — piggybacking goes silent on an idle queue), and
2. `EnvironmentWorker(...)` must receive only REAL constructor kwargs (`context_factory=` does not
   exist in the installed SDK — it raised TypeError at construction).

All CloudWatch/Anthropic surfaces are faked; no AWS, no network, no psycopg2.
"""
import asyncio
import contextlib
import inspect
from types import SimpleNamespace

import pytest

from agents.tools.base import Policy, Tool
from worker import worker

# ---------------------------------------------------------------------------- heartbeat_loop


class FakeCloudwatch:
    """Captures put_metric_data calls; optionally raises on chosen call numbers."""

    def __init__(self, fail_on: set[int] | None = None):
        self.calls: list[dict] = []
        self.attempts = 0
        self._fail_on = fail_on or set()

    def put_metric_data(self, **kwargs):
        self.attempts += 1
        if self.attempts in self._fail_on:
            raise RuntimeError(f"cloudwatch blip on attempt {self.attempts}")
        self.calls.append(kwargs)


async def _run_heartbeat_until(cw, *, predicate, interval_s=0.001, timeout=5.0):
    """Run heartbeat_loop as a task until `predicate()` holds, then cancel it cleanly."""
    task = asyncio.create_task(worker.heartbeat_loop(interval_s=interval_s, cloudwatch=cw))
    async with asyncio.timeout(timeout):
        while not predicate():
            assert not task.done(), f"heartbeat task died early: {task}"
            await asyncio.sleep(0.001)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    return task


@pytest.mark.unit
def test_heartbeat_emits_on_interval_and_stops_on_shutdown(monkeypatch):
    monkeypatch.setenv("CLOUDWATCH_METRICS", "1")
    cw = FakeCloudwatch()

    async def main():
        return await _run_heartbeat_until(cw, predicate=lambda: len(cw.calls) >= 3)

    task = asyncio.run(main())
    # Fired repeatedly on the interval (>= 3 emits), each the exact alarm-driving datapoint.
    assert len(cw.calls) >= 3
    for kwargs in cw.calls:
        assert kwargs["Namespace"] == "Uplift/Agents"
        assert kwargs["MetricData"] == [
            {"MetricName": "workers_polling", "Value": 1, "Unit": "Count"}
        ]
    # Clean shutdown: cancellation ended the task (no swallowed CancelledError, no zombie loop).
    assert task.cancelled()


@pytest.mark.unit
def test_heartbeat_continues_after_a_put_failure(monkeypatch):
    monkeypatch.setenv("CLOUDWATCH_METRICS", "1")
    cw = FakeCloudwatch(fail_on={2})  # second PutMetricData blips

    async def main():
        return await _run_heartbeat_until(cw, predicate=lambda: cw.attempts >= 4)

    asyncio.run(main())
    # Attempt 2 raised, the loop logged-and-continued: later attempts still landed.
    assert cw.attempts >= 4
    assert len(cw.calls) >= 3


@pytest.mark.unit
def test_heartbeat_is_a_noop_without_the_metrics_gate(monkeypatch):
    # CLOUDWATCH_METRICS unset -> returns immediately, zero emits (dev/tests stay AWS-free).
    monkeypatch.delenv("CLOUDWATCH_METRICS", raising=False)
    cw = FakeCloudwatch()
    asyncio.run(worker.heartbeat_loop(interval_s=0.001, cloudwatch=cw))
    assert cw.attempts == 0


@pytest.mark.unit
def test_heartbeat_interval_env_parsing(monkeypatch):
    monkeypatch.delenv("WORKER_HEARTBEAT_SECONDS", raising=False)
    assert worker._heartbeat_interval_s() == worker.DEFAULT_HEARTBEAT_INTERVAL_S
    monkeypatch.setenv("WORKER_HEARTBEAT_SECONDS", "12.5")
    assert worker._heartbeat_interval_s() == 12.5
    # Junk and non-positive values fall back — a bad env value must never block startup.
    for junk in ("nope", "", "0", "-3"):
        monkeypatch.setenv("WORKER_HEARTBEAT_SECONDS", junk)
        assert worker._heartbeat_interval_s() == worker.DEFAULT_HEARTBEAT_INTERVAL_S


@pytest.mark.unit
def test_emit_polling_metric_gated_off_makes_no_client(monkeypatch):
    # Gate off -> emit is a no-op even with a client supplied (and never builds boto3).
    monkeypatch.delenv("CLOUDWATCH_METRICS", raising=False)
    cw = FakeCloudwatch()
    worker.emit_polling_metric(cw)
    assert cw.attempts == 0


# ------------------------------------------------------------------- run() wiring (real kwargs)


def _patch_run_env(monkeypatch):
    monkeypatch.setenv("UPLIFT_ENV_ID", "env_test")
    monkeypatch.setenv("UPLIFT_ENV_KEY", "ek_test")
    monkeypatch.delenv("CLOUDWATCH_METRICS", raising=False)
    # Keep build_clients_from_env on the all-stub path: no DB, no Cortex, no spec generator.
    for var in (
        "UPLIFT_DB_URL", "DB_USER", "DB_PASS", "DB_HOST", "CUBE_ENDPOINT",
        "CORTEX_S3_BUCKET", "CORTEX_LOCAL_DIR", "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


class _FakeAsyncAnthropic:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.unit
def test_run_constructs_environment_worker_with_only_real_kwargs(monkeypatch):
    """Drive the REAL run() with a fake EnvironmentWorker capturing kwargs, then validate every
    captured kwarg against the INSTALLED SDK's constructor signature — if a nonexistent kwarg
    (e.g. the removed `context_factory=`) ever creeps back in, this fails offline."""
    import anthropic
    import anthropic.lib.environments as envmod

    real_params = set(inspect.signature(envmod.EnvironmentWorker.__init__).parameters)
    captured: dict = {}

    class FakeWorker:
        def __init__(self, client, **kwargs):
            captured["client"] = client
            captured["kwargs"] = kwargs

        async def run(self):
            captured["ran"] = True

    monkeypatch.setattr(envmod, "EnvironmentWorker", FakeWorker)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAsyncAnthropic)
    _patch_run_env(monkeypatch)

    asyncio.run(worker.run())

    assert captured["ran"] is True
    kwargs = captured["kwargs"]
    # The brief's verified bug: this kwarg does not exist on the installed SDK.
    assert "context_factory" not in kwargs
    # Every kwarg we pass must be a REAL constructor parameter of the installed SDK.
    unknown = set(kwargs) - real_params
    assert not unknown, f"EnvironmentWorker got nonexistent kwargs: {unknown}"
    assert kwargs["environment_id"] == "env_test"
    assert kwargs["environment_key"] == "ek_test"
    assert kwargs["workdir"] == "/workspace"
    assert callable(kwargs["tools"])  # the per-claimed-session factory (the SDK's real seam)
    # The worker client was built with the ENVIRONMENT key, never the org API key.
    assert captured["client"].kwargs == {"auth_token": "ek_test"}


@pytest.mark.unit
def test_run_starts_heartbeat_alongside_poll_loop_and_cancels_it_on_exit(monkeypatch):
    import anthropic
    import anthropic.lib.environments as envmod

    events: list[str] = []

    async def fake_heartbeat(**kwargs):
        events.append("started")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            events.append("cancelled")
            raise

    class FakeWorker:
        def __init__(self, client, **kwargs):
            pass

        async def run(self):
            await asyncio.sleep(0)  # let the heartbeat task actually start
            events.append("poll-loop-finished")

    monkeypatch.setattr(worker, "heartbeat_loop", fake_heartbeat)
    monkeypatch.setattr(envmod, "EnvironmentWorker", FakeWorker)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAsyncAnthropic)
    _patch_run_env(monkeypatch)

    asyncio.run(worker.run())
    # Structured concurrency: heartbeat lives exactly as long as the poll loop.
    assert events == ["started", "poll-loop-finished", "cancelled"]


@pytest.mark.unit
def test_run_heartbeat_cancelled_even_when_poll_loop_raises(monkeypatch):
    import anthropic
    import anthropic.lib.environments as envmod

    events: list[str] = []

    async def fake_heartbeat(**kwargs):
        events.append("started")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            events.append("cancelled")
            raise

    class FakeWorker:
        def __init__(self, client, **kwargs):
            pass

        async def run(self):
            await asyncio.sleep(0)
            raise RuntimeError("fatal 4xx from aiter_work")

    monkeypatch.setattr(worker, "heartbeat_loop", fake_heartbeat)
    monkeypatch.setattr(envmod, "EnvironmentWorker", FakeWorker)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAsyncAnthropic)
    _patch_run_env(monkeypatch)

    with pytest.raises(RuntimeError, match="fatal 4xx"):
        asyncio.run(worker.run())
    # The heartbeat died WITH the poll loop — a dead loop can never keep feeding the metric.
    assert events == ["started", "cancelled"]


@pytest.mark.unit
def test_run_emits_no_heartbeat_when_worker_construction_fails(monkeypatch):
    """The brief's crash-loop finding: a DOA worker (constructor raises) must emit ZERO
    heartbeats — construction happens BEFORE the heartbeat task starts."""
    import anthropic
    import anthropic.lib.environments as envmod

    events: list[str] = []

    async def fake_heartbeat(**kwargs):
        events.append("started")

    class ExplodingWorker:
        def __init__(self, client, **kwargs):
            raise TypeError("unexpected keyword argument 'context_factory'")

    monkeypatch.setattr(worker, "heartbeat_loop", fake_heartbeat)
    monkeypatch.setattr(envmod, "EnvironmentWorker", ExplodingWorker)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAsyncAnthropic)
    _patch_run_env(monkeypatch)

    with pytest.raises(TypeError):
        asyncio.run(worker.run())
    assert events == []  # the alarm sees a clean, immediate flatline — no flapping


# ------------------------------------------------------- per-session tenant binding (tools seam)


class _EchoTool(Tool):
    name = "echo"
    description = "test echo"
    input_schema = {"type": "object", "properties": {}}
    policy = Policy.AUTO

    def _execute(self, ctx, **kwargs):
        return {"tenant": ctx.tenant_id, "agent": ctx.agent, "kwargs": kwargs}


def _fake_session_env(metadata, session_id="sess_1"):
    """An AgentToolContext stand-in: .client (env-key-scoped sub-client) + .session_id."""
    retrieved: list[str] = []

    async def retrieve(sid):
        retrieved.append(sid)
        return SimpleNamespace(metadata=metadata)

    sessions = SimpleNamespace(retrieve=retrieve)
    env = SimpleNamespace(
        client=SimpleNamespace(beta=SimpleNamespace(sessions=sessions)),
        session_id=session_id,
    )
    return env, retrieved


@pytest.mark.unit
def test_session_bound_tool_binds_tenant_from_session_metadata():
    env, retrieved = _fake_session_env({"tenant_id": "tenant-A", "agent": "nadia"})
    binding = worker.SessionToolBinding(env, {"db": None, "rag": None, "greenlight": None})
    tool = worker.SessionBoundTool(_EchoTool(), binding)

    assert tool.name == "echo"
    assert tool.to_dict()["type"] == "custom"  # MA custom-tool definition shape

    async def main():
        first = await tool.call({"x": 1})
        second = await tool.call({"x": 2})
        return first, second

    import json

    first, second = (json.loads(r) for r in asyncio.run(main()))
    # THE TRUST RULE: tenant came from the session metadata, per call.
    assert first["result"] == {"tenant": "tenant-A", "agent": "nadia", "kwargs": {"x": 1}}
    assert second["result"]["kwargs"] == {"x": 2}
    # The session metadata was fetched ONCE (cached for the session's remaining calls).
    assert retrieved == ["sess_1"]


@pytest.mark.unit
def test_session_binding_builds_a_fresh_context_per_call():
    env, _ = _fake_session_env({"tenant_id": "tenant-A"})
    binding = worker.SessionToolBinding(env, {"db": None})

    async def main():
        return await binding.context(), await binding.context()

    ctx_a, ctx_b = asyncio.run(main())
    assert ctx_a is not ctx_b  # build_context per call — never shared mutable context state
    assert ctx_a.tenant_id == ctx_b.tenant_id == "tenant-A"


@pytest.mark.unit
def test_session_binding_refuses_a_session_without_tenant_id():
    env, _ = _fake_session_env({})  # no tenant stamped — must fail loudly, never default
    binding = worker.SessionToolBinding(env, {"db": None})
    with pytest.raises(RuntimeError, match="no tenant_id"):
        asyncio.run(binding.context())


@pytest.mark.unit
def test_session_tools_factory_wraps_the_full_registry_per_session():
    env, retrieved = _fake_session_env({"tenant_id": "tenant-A"})
    factory = worker.session_tools_factory({"db": None})
    tools = factory(env)
    # One wrapper per registered tool, names preserved for the SDK's name-keyed dispatch.
    assert [t.name for t in tools] == [t.name for t in worker.TOOLS]
    # The factory itself made NO network call (sync seam) — metadata is fetched on first use.
    assert retrieved == []


@pytest.mark.unit
def test_session_bound_side_effecting_tool_still_only_proposes():
    """Draft gate: through the new wrapper, an ALWAYS_ASK tool never executes — it proposes."""
    import json

    env, _ = _fake_session_env({"tenant_id": "tenant-A"})
    binding = worker.SessionToolBinding(env, {"db": None, "greenlight": None})
    send = next(t for t in worker.TOOLS if t.name == "send_email")
    tool = worker.SessionBoundTool(send, binding)

    result = json.loads(
        asyncio.run(tool.call({"to": "x@example.com", "subject": "hi", "body": "draft"}))
    )
    assert result["status"] == "pending_approval"
