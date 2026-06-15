"""Self-upgrading rosters — agents that never go stale (2026-06-14).

Managed-Agents specialists + coordinator are created once per tenant with the code's specs frozen
in (model tier, system prompt, AND every tool's input schema; the coordinator pins specialist
versions at create time). A later code change to any of those does NOT reach already-provisioned
tenants — caught live when a tenant created 2026-06-10 kept calling `draft_email` with the old
schema after the fix shipped.

This module makes upgrades automatic with no human in the loop:

  * `current_roster_version()` — a deterministic hash over the WHOLE roster (each spec's name,
    model, system prompt + every granted tool's full spec including input_schema) and the
    coordinator. ANY code change to a spec bumps the hash — nobody has to "remember to version".
  * `provision_roster(...)` — create a fresh roster + coordinator and stamp the tenant's row with
    the version (+ clear the stale session). The single create path, used by first-provision AND
    upgrade.
  * `maybe_upgrade_roster(...)` — called as the conversation is built: if the tenant's stamped
    version != the current code version, re-provision transparently before the turn is served.
    Every tenant self-heals on its next chat after a deploy — spread across traffic (no thundering
    herd), no batch job. A per-tenant lock prevents a double-provision; a failed upgrade degrades
    to the existing coordinator (no worse than before) and retries next turn.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import defaultdict
from typing import Any

log = logging.getLogger("agents.provisioning")

# Failure backoff: a persistent provisioning failure (e.g. a per-environment agent limit, a flapping
# MA endpoint) must NOT re-mint a fresh roster on EVERY chat turn — that floods orphans and hammers
# MA. After a failed upgrade to a given version, skip re-attempting THAT version for this tenant for
# the cooldown; serve the existing (stale) coordinator meanwhile. {tenant_id: (version, monotonic)}.
_UPGRADE_BACKOFF_SECONDS = 300.0
_failed_upgrade: dict[str, tuple[str, float]] = {}

# Hash prefix — bump only if the hashing SCHEME changes (forces a one-time re-provision of all
# tenants), never for content (content auto-bumps the digest).
_VERSION_SCHEME = "rv1"

# Computed once per process (the roster is static code) — cheap, deterministic.
_cached_version: str | None = None
_version_lock = threading.Lock()

# Per-tenant upgrade locks: serialize an upgrade for ONE tenant so two concurrent first-turns can't
# both re-provision. Never gates another tenant or any non-upgrade work.
_tenant_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


def _spec_fingerprint(spec: Any) -> dict:
    """The version-relevant surface of one AgentSpec: identity + behaviour + the EXACT tool specs
    the agent is created with (so a tool input-schema change bumps the version too)."""
    from .tools import registry  # noqa: PLC0415 — lazy: keep import-time cheap / avoid cycles

    tools = [registry.resolve(name).to_spec() for name in (getattr(spec, "tools", None) or [])]
    tools.sort(key=lambda t: t.get("name", ""))
    return {
        "name": spec.name,
        "model": spec.model,
        "system": spec.system,
        "tools": tools,
    }


def current_roster_version() -> str:
    """A stable fingerprint of the current code's roster + coordinator. Same code -> same string;
    any spec/model/prompt/tool-schema change -> a different string."""
    global _cached_version
    if _cached_version is not None:
        return _cached_version
    with _version_lock:
        if _cached_version is None:
            from .coordinator import COORDINATOR  # noqa: PLC0415 — lazy (import-safety)
            from .roster import roster  # noqa: PLC0415

            payload = [_spec_fingerprint(s) for s in roster()]
            payload.append(_spec_fingerprint(COORDINATOR))
            blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
            digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
            _cached_version = f"{_VERSION_SCHEME}-{digest}"
    return _cached_version


def provision_roster(runtime: Any, store: Any, tenant_id: str, *,
                     environment_id: str | None, workspace_id: str | None) -> dict:
    """Create a FRESH roster + coordinator on `runtime` and stamp the tenant's row.

    The one create path (first-provision and upgrade). Persists the new coordinator_id + the
    current roster_version, and CLEARS session_id (the old session belongs to the old coordinator).
    Orphaned old agents are harmless (GC out of band). Raises on any create failure so the caller
    can fall back / retry; nothing is persisted until the full roster + coordinator succeed.
    """
    from .coordinator import COORDINATOR  # noqa: PLC0415
    from .roster import roster  # noqa: PLC0415

    agent_ids = [runtime.create_agent(spec) for spec in roster()]
    coordinator_id = runtime.create_coordinator(COORDINATOR, agent_ids)
    version = current_roster_version()
    store.upsert(tenant_id, workspace_id, environment_id, coordinator_id, roster_version=version)
    # Clear the stale session AFTER the row points at the new coordinator (best-effort; a store
    # without set_session_id — older protocol — simply skips it).
    setter = getattr(store, "set_session_id", None)
    if callable(setter):
        setter(tenant_id, None)
    return {"coordinator_id": coordinator_id, "roster_version": version,
            "environment_id": environment_id, "workspace_id": workspace_id}


def maybe_upgrade_roster(runtime: Any, store: Any, row: dict, tenant_id: str) -> str:
    """Return the coordinator_id to serve THIS turn with, upgrading the tenant's roster first if it
    is stale. No-op (returns the existing coordinator) when the stamp is already current. A failed
    upgrade logs and degrades to the existing coordinator — never breaks the turn."""
    current = current_roster_version()
    if row.get("roster_version") == current:
        return row["coordinator_id"]
    # Backoff: a recent failure to reach THIS version for this tenant -> don't re-attempt yet
    # (serve stale), so a persistent failure can't flood orphans / hammer MA every turn.
    failed = _failed_upgrade.get(tenant_id)
    if failed and failed[0] == current and (time.monotonic() - failed[1]) < _UPGRADE_BACKOFF_SECONDS:
        return row["coordinator_id"]
    with _tenant_locks[tenant_id]:
        # Re-read under the lock: a peer turn may have just upgraded this tenant.
        fresh = store.get(tenant_id) or row
        if fresh.get("roster_version") == current:
            _failed_upgrade.pop(tenant_id, None)
            return fresh.get("coordinator_id") or row["coordinator_id"]
        try:
            result = provision_roster(
                runtime, store, tenant_id,
                environment_id=fresh.get("environment_id") or row.get("environment_id"),
                workspace_id=fresh.get("workspace_id") or row.get("workspace_id"),
            )
            _failed_upgrade.pop(tenant_id, None)  # success clears the backoff
            log.info(
                "roster auto-upgrade: tenant=%s %s -> %s (new coordinator=%s)",
                tenant_id, row.get("roster_version") or "unstamped", current,
                result["coordinator_id"],
            )
            return result["coordinator_id"]
        except Exception:  # noqa: BLE001 — never fail a chat turn on an upgrade hiccup
            _failed_upgrade[tenant_id] = (current, time.monotonic())  # arm the cooldown
            log.exception(
                "roster auto-upgrade FAILED for tenant=%s — serving the existing coordinator, "
                "backing off %.0fs before re-attempting", tenant_id, _UPGRADE_BACKOFF_SECONDS)
            return row["coordinator_id"]
