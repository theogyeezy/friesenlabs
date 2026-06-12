"""Unit: the per-tenant Conversation cache (conv/cache.py) — the /chat cache-hygiene contract.

1. LOCK SCOPE: no cache lock is held across the (network-I/O) Conversation build — a slow build
   for tenant A must never serialize tenant B (the old in-asgi global mutex did exactly that);
   concurrent duplicate builds re-validate after the build and exactly one wins.
2. Per-tenant TURN locks serialize concurrent turns on ONE tenant's MA session only.
3. LRU + TTL eviction bounds the cache (size/TTL injectable; env defaults 200 / 30 min).
4. Fresh 'today' per turn: the Conversation resolves a callable provider per access — a cached
   conversation never freezes date math at construction time.
"""
import threading
from datetime import date

import pytest

from conv.cache import (
    DEFAULT_MAX_ENTRIES,
    DEFAULT_TTL_SECONDS,
    TenantConversationCache,
)


class _FakeConvo:
    def __init__(self, tenant_id, fail_terminated_once=False):
        self.tenant_id = tenant_id
        self.sent: list[str] = []
        self._fail = fail_terminated_once

    def send(self, message, **kwargs):
        if self._fail:
            self._fail = False
            raise RuntimeError(f"MA session sess-{self.tenant_id} terminated (irreversible)")
        self.sent.append(message)
        return {"answer": f"ok:{self.tenant_id}", "message": message}


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _cache(builder=None, **kw):
    built = []

    def build(tenant_id):
        convo = _FakeConvo(tenant_id) if builder is None else builder(tenant_id)
        if convo is not None:
            built.append(convo)
        return convo

    cache = TenantConversationCache(build, max_entries=kw.pop("max_entries", 4),
                                    ttl_seconds=kw.pop("ttl_seconds", 100.0), **kw)
    return cache, built


# ---------------------------------------------------------------- basics
@pytest.mark.unit
def test_not_provisioned_returns_none_and_is_never_cached():
    cache, built = _cache(builder=lambda t: None)
    assert cache("tenant-A") is None
    assert cache("tenant-A") is None
    assert built == []


@pytest.mark.unit
def test_one_conversation_per_tenant_across_requests():
    cache, built = _cache()
    proxy1 = cache("tenant-A")
    proxy2 = cache("tenant-A")
    proxy1.send("hi")
    proxy2.send("again")
    assert len(built) == 1  # turn continuity: ONE MA session across requests
    assert built[0].sent == ["hi", "again"]


@pytest.mark.unit
def test_env_defaults_are_sane():
    assert DEFAULT_MAX_ENTRIES == 200
    assert DEFAULT_TTL_SECONDS == 1800.0


@pytest.mark.unit
def test_env_junk_falls_back_to_defaults(monkeypatch):
    monkeypatch.setenv("UPLIFT_CONV_CACHE_MAX", "banana")
    monkeypatch.setenv("UPLIFT_CONV_CACHE_TTL_SECONDS", "-5")
    cache = TenantConversationCache(lambda t: _FakeConvo(t))
    assert cache._max == DEFAULT_MAX_ENTRIES
    assert cache._ttl == DEFAULT_TTL_SECONDS


@pytest.mark.unit
def test_env_overrides_apply(monkeypatch):
    monkeypatch.setenv("UPLIFT_CONV_CACHE_MAX", "3")
    monkeypatch.setenv("UPLIFT_CONV_CACHE_TTL_SECONDS", "60")
    cache = TenantConversationCache(lambda t: _FakeConvo(t))
    assert cache._max == 3
    assert cache._ttl == 60.0


# ---------------------------------------------------------------- lock scope (hygiene #1)
@pytest.mark.unit
def test_slow_build_for_one_tenant_never_blocks_another():
    """The old bug: the global mutex was held across the Anthropic build, serializing ALL
    tenants. Here tenant A's build parks on an event while tenant B leases straight through."""
    a_building = threading.Event()
    release_a = threading.Event()

    def builder(tenant_id):
        if tenant_id == "tenant-A":
            a_building.set()
            assert release_a.wait(timeout=5), "test deadlock: release_a never set"
        return _FakeConvo(tenant_id)

    cache, _built = _cache(builder=builder)
    results = {}
    t = threading.Thread(target=lambda: results.update(a=cache("tenant-A")))
    t.start()
    assert a_building.wait(timeout=5)
    # Tenant A is mid-build (simulated network I/O). Tenant B must proceed immediately.
    proxy_b = cache("tenant-B")
    assert proxy_b is not None
    assert proxy_b.send("hello")["answer"] == "ok:tenant-B"
    release_a.set()
    t.join(timeout=5)
    assert results["a"] is not None
    assert results["a"].send("hi")["answer"] == "ok:tenant-A"


@pytest.mark.unit
def test_concurrent_duplicate_builds_revalidate_and_one_wins():
    barrier = threading.Barrier(2, timeout=5)

    def builder(tenant_id):
        barrier.wait()  # both threads are INSIDE the unlocked build simultaneously
        return _FakeConvo(tenant_id)

    cache, built = _cache(builder=builder)
    leases = []
    threads = [threading.Thread(target=lambda: leases.append(cache.lease("tenant-A")))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert len(built) == 2          # both builds ran (no lock across network I/O)...
    assert leases[0] is leases[1]   # ...but re-validation made exactly ONE serve both callers
    assert cache.lease("tenant-A") is leases[0]  # and it is the cached one


# ---------------------------------------------------------------- per-tenant turn locks (#2)
@pytest.mark.unit
def test_turns_for_one_tenant_serialize():
    overlap = {"active": 0, "max": 0}
    lock = threading.Lock()

    class _SlowConvo(_FakeConvo):
        def send(self, message, **kwargs):
            with lock:
                overlap["active"] += 1
                overlap["max"] = max(overlap["max"], overlap["active"])
            threading.Event().wait(0.02)  # hold the turn briefly
            with lock:
                overlap["active"] -= 1
            return super().send(message, **kwargs)

    cache, _ = _cache(builder=lambda t: _SlowConvo(t))
    proxy = cache("tenant-A")
    threads = [threading.Thread(target=proxy.send, args=(f"m{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert overlap["max"] == 1  # never two concurrent stream-drains on one MA session


# ---------------------------------------------------------------- eviction (#3)
@pytest.mark.unit
def test_lru_eviction_bounds_the_cache():
    clock = _Clock()
    cache, built = _cache(max_entries=2, clock=clock)
    cache("tenant-A").send("a")
    clock.now += 1
    cache("tenant-B").send("b")
    clock.now += 1
    cache("tenant-C").send("c")  # overflow -> tenant-A (LRU) evicted
    assert len(built) == 3
    clock.now += 1
    cache("tenant-B").send("b2")  # still cached — no rebuild
    assert len(built) == 3
    cache("tenant-A").send("a2")  # evicted — rebuilds
    assert len(built) == 4


@pytest.mark.unit
def test_lru_order_follows_last_use_not_insertion():
    clock = _Clock()
    cache, built = _cache(max_entries=2, clock=clock)
    cache("tenant-A").send("a")
    clock.now += 1
    cache("tenant-B").send("b")
    clock.now += 1
    cache("tenant-A").send("a-again")  # A is now most-recently-used
    clock.now += 1
    cache("tenant-C").send("c")        # overflow -> B (least recently USED) evicted
    clock.now += 1
    cache("tenant-A").send("a3")
    assert len(built) == 3             # A survived
    cache("tenant-B").send("b2")
    assert len(built) == 4             # B was the evictee


@pytest.mark.unit
def test_ttl_eviction_drops_idle_conversations():
    clock = _Clock()
    cache, built = _cache(ttl_seconds=10.0, clock=clock)
    cache("tenant-A").send("a")
    clock.now += 9.0
    cache("tenant-A").send("a2")   # idle 9s < 10s — same conversation, TTL refreshed
    assert len(built) == 1
    clock.now += 10.0
    cache("tenant-A").send("a3")   # idle 10s >= 10s — expired, rebuilt
    assert len(built) == 2


@pytest.mark.unit
def test_turn_locks_prune_with_eviction_but_never_while_held():
    clock = _Clock()
    cache, _built = _cache(max_entries=1, clock=clock)
    cache("tenant-A").send("a")
    lock_a = cache.turn_lock("tenant-A")
    with lock_a:  # a turn is in flight for A
        clock.now += 1
        cache("tenant-B").send("b")  # evicts A's entry; A's HELD lock must survive the sweep
        assert cache.turn_lock("tenant-A") is lock_a
    clock.now += 1
    cache("tenant-C").send("c")      # next sweep: A's lock is no longer held — pruned
    assert cache.turn_lock("tenant-A") is not lock_a


# ---------------------------------------------------------------- terminated-session recovery
@pytest.mark.unit
def test_terminated_session_rebuilds_once_and_replays_the_turn():
    # Built manually so only the FIRST conversation is terminated.
    built = []

    def build(tenant_id):
        convo = _FakeConvo(tenant_id, fail_terminated_once=not built)
        built.append(convo)
        return convo

    cache = TenantConversationCache(build, max_entries=4, ttl_seconds=100.0)
    proxy = cache("tenant-A")
    out = proxy.send("hello")
    assert out["answer"] == "ok:tenant-A"
    assert len(built) == 2                 # dead session replaced exactly once
    assert built[0].sent == []             # the terminated convo never completed a turn
    assert built[1].sent == ["hello"]      # the rebuilt one served it


@pytest.mark.unit
def test_non_terminated_errors_propagate_without_rebuild():
    class _Broken(_FakeConvo):
        def send(self, message, **kwargs):
            raise RuntimeError("boom — an unrelated failure")

    cache, built = _cache(builder=lambda t: _Broken(t))
    proxy = cache("tenant-A")
    with pytest.raises(RuntimeError, match="boom"):
        proxy.send("hello")
    assert len(built) == 1  # no rebuild on arbitrary errors


# ---------------------------------------------------------------- fresh 'today' per turn (#4)
@pytest.mark.unit
def test_conversation_resolves_callable_today_fresh_per_access():
    from conv.session import Conversation

    days = iter([date(2026, 6, 10), date(2026, 6, 11)])
    convo = Conversation(tenant_id="tenant-A", today=lambda: next(days))
    assert convo.today == date(2026, 6, 10)
    assert convo.today == date(2026, 6, 11)  # resolved per access — never frozen


@pytest.mark.unit
def test_conversation_accepts_a_fixed_date_for_deterministic_tests():
    from conv.session import Conversation

    convo = Conversation(tenant_id="tenant-A", today=date(2026, 6, 10))
    assert convo.today == date(2026, 6, 10)
    assert convo.today == date(2026, 6, 10)


@pytest.mark.unit
def test_asgi_factory_hands_the_conversation_a_live_today_provider():
    """The frozen-'today' fix end-to-end: the asgi factory passes a CALLABLE provider, so a
    cached Conversation's date math moves with the clock instead of freezing at build time."""
    from agents.runtime import FakeRuntime
    from agents.workspace_store import InMemoryWorkspaceStore
    from api.asgi import make_conversation_factory

    store = InMemoryWorkspaceStore()
    store.upsert("tenant-A", "ws-A", "env-A", "coord-A")
    days = iter([date(2026, 6, 10), date(2026, 6, 11)])
    factory = make_conversation_factory(
        workspace_store=store, runtime_factory=lambda row: FakeRuntime(),
        today=lambda: next(days),
    )
    convo = factory("tenant-A")
    assert convo.today == date(2026, 6, 10)
    assert convo.today == date(2026, 6, 11)  # a NEW date on the next turn — not frozen


# --------------------------------------------------------------------------- continue_turn proxy
# Live finding (2026-06-12, round 5): POST /chat/continue 501'd in prod — the factory returns
# THIS proxy, which only exposed send(), so the route's hasattr(convo, 'continue_turn') guard
# fired. The proxy must pass continue_turn through under the SAME per-tenant turn lock.

@pytest.mark.unit
def test_cached_conversation_proxies_continue_turn():
    class _ContinuableConvo(_FakeConvo):
        def __init__(self, tenant_id):
            super().__init__(tenant_id)
            self.continues = 0

        def continue_turn(self):
            self.continues += 1
            return {"answer": "settled", "settled": True}

    built = {}

    def builder(tenant_id):
        built[tenant_id] = _ContinuableConvo(tenant_id)
        return built[tenant_id]

    cache = TenantConversationCache(builder)
    proxy = cache("tenant-a")
    proxy.send("hello")
    out = proxy.continue_turn()
    assert out == {"answer": "settled", "settled": True}
    assert built["tenant-a"].continues == 1
    # The route's capability check must see the method on the PROXY.
    assert hasattr(proxy, "continue_turn")
