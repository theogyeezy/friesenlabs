"""Persisted control settings — the shared backing for the kill switch + the autonomy dial.

The accountability pitch is only real if a flip SURVIVES the process and is SEEN by every API
task: an in-memory `KillSwitch` on one Fargate task is invisible to its peer behind the ALB.
This module makes both controls ride ONE shared Postgres row set — the EXISTING `tenant_settings`
table (seeded at provisioning by `signup/tenant_defaults.py`; `killswitch_engaged` appended in
`db/schema.sql`) — through a `ControlSettingsStore`:

  * `PgControlSettingsStore` (api/pg_clients.py) — the real store: per-op
    `SET LOCAL app.current_tenant` transaction as the non-owner crm_app role, exactly the
    PgApprovalStore pattern, so RLS scopes every read/write.
  * `InMemoryControlSettings` (here) — the offline/dev store (same semantics, no DB).

SCOPES. The kill switch has two:
  * "tenant" — the tenant's own `tenant_settings` row. Pauses that tenant only.
  * "global" — the reserved all-zeros control row (`GLOBAL_CONTROL_TENANT`). Pauses EVERY
    tenant. uuid4 minting always sets version/variant bits, so a provisioned tenant can never
    collide with the sentinel; the row is reachable only because the store deliberately scopes
    a transaction to it — request-path tenant scoping still comes ONLY from the verified claim.

FRESHNESS. `PersistedKillSwitch` / `PersistedAutonomyDial` are read-through facades with a short
TTL cache (default 2s): a flip on instance A is seen by instance B within the TTL — "within
seconds" without a per-action DB round-trip on the hot gate path. A local write invalidates the
local cache, so the flipping instance reads its own write immediately.

THE TRUST RULE: every tenant_id flowing in here is the verified Cognito JWT claim (threaded by
the routes/gate); nothing reads env, headers, or payloads for it.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Protocol

from .types import Level

# The reserved GLOBAL control row's tenant key (see module docstring + db/schema.sql comment).
GLOBAL_CONTROL_TENANT = "00000000-0000-0000-0000-000000000000"

# How long a cached read may serve before re-hitting the store. Short enough that a peer
# instance's flip lands "within seconds"; long enough to keep the gate path off the DB.
DEFAULT_CONTROL_TTL_S = 2.0

# The persisted autonomy text values (tenant_settings.autonomy_level) — Level enum values.
VALID_LEVELS = tuple(level.value for level in Level)


class ControlSettingsStore(Protocol):
    """One tenant's control row: {tenant_id, autonomy_level, killswitch_engaged}."""

    def get(self, tenant_id) -> dict | None: ...
    def set_killswitch(self, tenant_id, engaged: bool) -> None: ...
    def set_autonomy(self, tenant_id, level: str) -> None: ...


class InMemoryControlSettings:
    """Offline control-settings store (the real one is PgControlSettingsStore over Aurora).

    Mirrors the Pg upsert semantics: a set_* on an unseeded tenant creates the row with the
    schema defaults (autonomy 'L1', killswitch disengaged) before applying the change.
    """

    def __init__(self):
        self._rows: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _row(self, tenant_id) -> dict:
        key = str(tenant_id)
        if key not in self._rows:
            self._rows[key] = {"tenant_id": key, "autonomy_level": "L1",
                               "killswitch_engaged": False}
        return self._rows[key]

    def get(self, tenant_id) -> dict | None:
        with self._lock:
            row = self._rows.get(str(tenant_id))
            return dict(row) if row else None

    def set_killswitch(self, tenant_id, engaged: bool) -> None:
        with self._lock:
            self._row(tenant_id)["killswitch_engaged"] = bool(engaged)

    def set_autonomy(self, tenant_id, level: str) -> None:
        if level not in VALID_LEVELS:
            raise ValueError(f"autonomy level must be one of {VALID_LEVELS}, got {level!r}")
        with self._lock:
            self._row(tenant_id)["autonomy_level"] = level


class _TtlCache:
    """A tiny thread-safe per-key TTL cache (monotonic clock injectable for tests)."""

    def __init__(self, ttl_seconds: float, clock: Callable[[], float]):
        self._ttl = float(ttl_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._items: dict[str, tuple[Any, float]] = {}

    def get(self, key: str, load: Callable[[], Any]) -> Any:
        now = self._clock()
        with self._lock:
            hit = self._items.get(key)
            if hit is not None and hit[1] > now:
                return hit[0]
        value = load()  # outside the lock — the store does its own (pooled) locking
        with self._lock:
            self._items[key] = (value, now + self._ttl)
        return value

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)


class PersistedKillSwitch:
    """The kill switch over shared persistence — gate-compatible (`is_paused`) + route surface.

    `is_paused(tenant)` consults BOTH scopes (global wins) through the TTL cache, so every API
    task sees a peer's flip within `ttl_seconds`. `set(...)` writes through and invalidates the
    local cache (read-your-own-write on the flipping instance). Same `status`/`set` surface as
    the in-memory `KillSwitch`, so api/routes_control.py serves either interchangeably.
    """

    def __init__(self, store: ControlSettingsStore, *,
                 ttl_seconds: float = DEFAULT_CONTROL_TTL_S,
                 clock: Callable[[], float] = time.monotonic):
        self._store = store
        self._cache = _TtlCache(ttl_seconds, clock)

    def _engaged(self, tenant_key: str) -> bool:
        def load() -> bool:
            row = self._store.get(tenant_key)
            return bool(row.get("killswitch_engaged")) if row else False
        return self._cache.get(f"ks:{tenant_key}", load)

    # --- the gate/approval-decide surface (api/control/gate.py, api/app.py) ---
    def is_paused(self, tenant_id: str) -> bool:
        return self._engaged(GLOBAL_CONTROL_TENANT) or self._engaged(str(tenant_id))

    # --- the /control/killswitch route surface ---
    def status(self, tenant_id: str) -> dict:
        if self._engaged(GLOBAL_CONTROL_TENANT):
            return {"engaged": True, "scope": "global"}
        if self._engaged(str(tenant_id)):
            return {"engaged": True, "scope": "tenant"}
        return {"engaged": False, "scope": "tenant"}

    def set(self, tenant_id: str, engaged: bool, *, scope: str = "tenant") -> None:
        """Write-through flip. AUTHORIZATION IS THE ROUTE'S JOB (api/routes_control.py):
        callers must already have checked who may flip the requested scope."""
        if scope not in ("tenant", "global"):
            raise ValueError(f"scope must be 'tenant' or 'global', got {scope!r}")
        target = GLOBAL_CONTROL_TENANT if scope == "global" else str(tenant_id)
        self._store.set_killswitch(target, bool(engaged))
        self._cache.invalidate(f"ks:{target}")


class PersistedAutonomyDial:
    """The autonomy dial over shared persistence (tenant_settings.autonomy_level).

    `provider` plugs into `AutonomyConfig.level_provider`, so the gate resolves the PERSISTED
    per-tenant level (TTL-cached) instead of the hardcoded default; `get`/`set` serve the
    /control/autonomy routes. An unseeded/invalid row resolves to None → the gate falls back to
    `AutonomyConfig.default_level` (L1), matching the provisioning seed.
    """

    def __init__(self, store: ControlSettingsStore, *,
                 default_level: Level = Level.L1,
                 ttl_seconds: float = DEFAULT_CONTROL_TTL_S,
                 clock: Callable[[], float] = time.monotonic):
        self._store = store
        self._default = default_level
        self._cache = _TtlCache(ttl_seconds, clock)

    def provider(self, tenant_id: str) -> Level | None:
        """`AutonomyConfig.level_provider` seam: the persisted level, or None when unseeded."""
        key = str(tenant_id)

        def load() -> Level | None:
            row = self._store.get(key)
            raw = (row or {}).get("autonomy_level")
            try:
                return Level(raw) if raw is not None else None
            except ValueError:
                return None  # junk in the column never crashes the gate — default applies
        return self._cache.get(f"al:{key}", load)

    def get(self, tenant_id: str) -> Level:
        level = self.provider(tenant_id)
        return level if level is not None else self._default

    def set(self, tenant_id: str, level: Level) -> None:
        self._store.set_autonomy(str(tenant_id), Level(level).value)
        self._cache.invalidate(f"al:{str(tenant_id)}")


class AutonomyDial:
    """In-memory dial over `AutonomyConfig.overrides` — the offline/unconfigured fallback.

    Writing here is instance-local by design (no DB to share); the gate sees the flip
    immediately because `autonomy.resolve` checks the same overrides dict.
    """

    def __init__(self, config):
        self._config = config

    def get(self, tenant_id: str) -> Level:
        from . import autonomy  # noqa: PLC0415 — sibling import kept lazy (no cycle at import)
        return autonomy.resolve(self._config, None, tenant_id)

    def set(self, tenant_id: str, level: Level) -> None:
        self._config.overrides[tenant_id] = Level(level)
