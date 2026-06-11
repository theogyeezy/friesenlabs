"""Per-tenant Conversation cache — the /chat front door's turn-continuity store (#147).

A fresh Conversation per request creates a fresh MA session per turn, so async thread reports —
resolved by the self-hosted worker BETWEEN turns — return into sessions no later turn ever reads.
Caching ONE long-lived Conversation per tenant keeps the MA session alive across turns; the
follow-up turn then surfaces the completed work. Safe to share per tenant: the tool clients are
tenant-scoped and every DB access runs its own SET LOCAL transaction (the pooled-store pattern).

Cache-hygiene contract (this module exists to honor it):

1. NO lock is ever held across Anthropic network I/O for cache MANAGEMENT. Building a
   Conversation calls `runtime.create_session` (a live MA call); the old in-asgi cache held its
   one global mutex around that build, serializing EVERY tenant behind any tenant's slow build.
   Here the registry mutex guards dict bookkeeping only (microseconds); builds run UNLOCKED and
   RE-VALIDATE under the mutex afterwards — a racing duplicate build loses and the winner's
   entry is used (the loser's MA session handle is simply dropped, harmless).
2. Per-tenant TURN locks serialize concurrent turns on ONE MA session — two interleaved
   stream-drains on the same session would corrupt both turns (the runtime's per-session dedupe
   ledger is not thread-safe, and the drain protocol is one-drainer-by-design). A turn lock
   serializes exactly one tenant; it never gates another tenant or the cache itself.
3. LRU + TTL eviction bounds the cache: `UPLIFT_CONV_CACHE_MAX` entries (default 200), idle TTL
   `UPLIFT_CONV_CACHE_TTL_SECONDS` (default 1800 = 30 min since last use). An evicted tenant
   transparently rebuilds on its next turn — durable state (approvals, views, workspace ids)
   lives in Greenlight/Aurora, never in the Conversation object.

Fresh-'today' note: this cache does NOT fix date math by itself — the factory must hand the
Conversation a CALLABLE `today` provider (see `conv.session.Conversation`), because a cached
Conversation outlives the day it was built on.

Import-safe and framework-free: fastapi is imported lazily only on the rare
"tenant agent plane unavailable" rebuild-failure path (mirrors the previous asgi behavior).
"""
from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

# Env knobs (read at construction; constructor kwargs win — the test seam).
ENV_CONV_CACHE_MAX = "UPLIFT_CONV_CACHE_MAX"
ENV_CONV_CACHE_TTL_SECONDS = "UPLIFT_CONV_CACHE_TTL_SECONDS"
DEFAULT_MAX_ENTRIES = 200
DEFAULT_TTL_SECONDS = 1800.0  # 30 minutes idle


def _env_positive(name: str, default: float) -> float:
    """A positive number from env; junk/unset/non-positive -> the default (a bad env value must
    never keep the API from booting)."""
    try:
        value = float(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


class _Entry:
    __slots__ = ("convo", "created_at", "last_used")

    def __init__(self, convo: Any, now: float):
        self.convo = convo
        self.created_at = now
        self.last_used = now


class CachedConversation:
    """Per-tenant send() proxy over the cache: serializes turns on the tenant's ONE MA session
    and transparently re-leases after eviction or a terminated session."""

    def __init__(self, cache: "TenantConversationCache", tenant_id: str):
        self._cache = cache
        self._tenant_id = tenant_id

    def send(self, message: str, **kwargs: Any):
        # Per-tenant serialization: two concurrent turns on ONE MA session would interleave two
        # stream-drains; chat turns are seconds-long and the lock is PER TENANT — it never
        # serializes other tenants (hygiene contract #2). The lease below may rebuild after an
        # eviction; that network I/O runs under THIS tenant's turn lock only, never the cache's
        # registry mutex (hygiene contract #1).
        with self._cache.turn_lock(self._tenant_id):
            convo = self._cache.lease(self._tenant_id)
            if convo is None:
                _raise_unavailable()
            try:
                return convo.send(message, **kwargs)
            except RuntimeError as e:
                if "terminated" not in str(e):
                    raise
                # The cached session died (irreversible) — rebuild ONCE on a fresh session.
                convo = self._cache.rebuild(self._tenant_id, dead=convo)
                return convo.send(message, **kwargs)


def _raise_unavailable():
    try:
        from fastapi import HTTPException  # noqa: PLC0415 — lazy; conv stays framework-free
    except ImportError:  # pragma: no cover — fastapi is always present on the API task
        raise RuntimeError("tenant agent plane unavailable") from None
    raise HTTPException(status_code=503, detail="tenant agent plane unavailable")


class TenantConversationCache:
    """tenant_id -> ONE long-lived Conversation, with per-tenant locking + LRU/TTL eviction.

    `build` is the conversation factory (tenant_id -> Conversation | None); None means "not
    provisioned" and is NEVER cached (the /chat route turns it into the graceful 503).
    `clock` is injectable (monotonic seconds) so eviction is unit-testable without sleeping.
    """

    def __init__(
        self,
        build: Callable[[str], Any],
        *,
        max_entries: int | None = None,
        ttl_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._build = build
        self._clock = clock
        self._max = int(max_entries if max_entries is not None
                        else _env_positive(ENV_CONV_CACHE_MAX, DEFAULT_MAX_ENTRIES))
        self._ttl = float(ttl_seconds if ttl_seconds is not None
                          else _env_positive(ENV_CONV_CACHE_TTL_SECONDS, DEFAULT_TTL_SECONDS))
        if self._max < 1:
            raise ValueError(f"max_entries must be >= 1, got {self._max}")
        if self._ttl <= 0:
            raise ValueError(f"ttl_seconds must be > 0, got {self._ttl}")
        # The REGISTRY mutex: guards the two dicts below, held for dict ops ONLY — never across
        # a build (network I/O) and never across a turn (hygiene contract #1).
        self._mutex = threading.Lock()
        self._entries: OrderedDict[str, _Entry] = OrderedDict()  # LRU order: oldest first
        self._turn_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------------ public surface
    def __call__(self, tenant_id: str):
        """The conversation-factory surface the /chat route calls: returns the per-tenant proxy,
        or None when the tenant is not provisioned (the route's graceful 503)."""
        if self.lease(tenant_id) is None:
            return None  # not provisioned — never cached
        return CachedConversation(self, tenant_id)

    def turn_lock(self, tenant_id: str) -> threading.Lock:
        """This tenant's turn-serialization lock (created on first use, pruned with eviction)."""
        with self._mutex:
            lock = self._turn_locks.get(tenant_id)
            if lock is None:
                lock = self._turn_locks[tenant_id] = threading.Lock()
            return lock

    def lease(self, tenant_id: str):
        """The tenant's live Conversation: cache hit refreshes LRU/TTL; miss or expiry builds —
        UNLOCKED (network I/O), then re-validates under the mutex (a racing build loses)."""
        now = self._clock()
        with self._mutex:
            entry = self._entries.get(tenant_id)
            if entry is not None and self._fresh(entry, now):
                entry.last_used = now
                self._entries.move_to_end(tenant_id)
                return entry.convo
            if entry is not None:
                del self._entries[tenant_id]  # expired — drop before rebuilding
        convo = self._build(tenant_id)  # NETWORK I/O — no lock held (hygiene contract #1)
        if convo is None:
            return None
        return self._adopt(tenant_id, convo)

    def rebuild(self, tenant_id: str, *, dead: Any = None):
        """Replace a dead conversation with a fresh one (terminated-session recovery). If another
        thread already replaced it (the entry is fresh and is not the dead object), reuse theirs
        instead of building a second session."""
        now = self._clock()
        with self._mutex:
            entry = self._entries.get(tenant_id)
            if entry is not None:
                if entry.convo is not dead and self._fresh(entry, now):
                    entry.last_used = now
                    self._entries.move_to_end(tenant_id)
                    return entry.convo  # someone else already rebuilt — reuse
                del self._entries[tenant_id]
        convo = self._build(tenant_id)  # NETWORK I/O — no lock held
        if convo is None:
            _raise_unavailable()
        return self._adopt(tenant_id, convo)

    # ------------------------------------------------------------------ internals
    def _fresh(self, entry: _Entry, now: float) -> bool:
        return (now - entry.last_used) < self._ttl

    def _adopt(self, tenant_id: str, convo: Any):
        """RE-VALIDATE after an unlocked build: if a concurrent build won the race, use the
        winner's entry and drop ours; otherwise insert and evict overflow/expiry."""
        now = self._clock()
        with self._mutex:
            entry = self._entries.get(tenant_id)
            if entry is not None and self._fresh(entry, now):
                entry.last_used = now
                self._entries.move_to_end(tenant_id)
                return entry.convo  # lost the race — the winner's conversation serves
            self._entries[tenant_id] = _Entry(convo, now)
            self._entries.move_to_end(tenant_id)
            self._evict_locked(now)
            return convo

    def _evict_locked(self, now: float) -> None:
        """TTL sweep + LRU overflow (mutex held — dict ops only). Turn locks for evicted tenants
        are pruned only when NOT held: a lock under an in-flight turn survives to the next sweep
        (never yanked out from under the turn)."""
        for tid in [t for t, e in self._entries.items() if not self._fresh(e, now)]:
            del self._entries[tid]
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)  # LRU: oldest first
        live = self._entries.keys()
        for tid in [t for t, lock in self._turn_locks.items()
                    if t not in live and not lock.locked()]:
            del self._turn_locks[tid]
