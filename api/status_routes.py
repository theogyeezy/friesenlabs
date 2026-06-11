"""Public, unauthenticated status endpoint — GET /public/status (and GET /api/status alias).

Replaces the web Status page's reliance on a bare /healthz that left every subsystem permanently
"unknown"/"degraded". This endpoint aggregates per-subsystem readiness from injected probe
callables — no live DB/network calls inside the route itself, which stays import-safe.

Probes are injected (StatusDeps). A ``None`` probe means the subsystem is not wired into this
deployment and is honestly reported as "unknown" — it does NOT drag the rollup below operational.

Rollup rule (the backlog complaint this fixes):
  * "down"      if any component is "down"
  * "degraded"  if any component is "degraded" (and none is "down")
  * "operational" otherwise (including when all unknowns + api operational)

The ``api`` component is always "operational" — if this endpoint answered, the API is up.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from fastapi import FastAPI

log = logging.getLogger("api.status")

# ---------------------------------------------------------------------------  types / constants

State = str  # "operational" | "degraded" | "down" | "unknown"

_OPERATIONAL = "operational"
_DEGRADED = "degraded"
_DOWN = "down"
_UNKNOWN = "unknown"

# Ranked severity (highest index = worst), used for rollup. "unknown" is EXCLUDED from rollup
# severity — it never drags the overall status down.
_SEVERITY_RANK: dict[State, int] = {
    _OPERATIONAL: 0,
    _UNKNOWN: 0,   # same weight as operational in the rollup
    _DEGRADED: 1,
    _DOWN: 2,
}


@dataclass
class StatusDeps:
    """Injectable probe callables, one per subsystem.

    Each probe is a zero-arg callable returning a state string ("operational", "degraded",
    "down"). When a probe raises, the component is reported "down" with the error as detail.
    ``None`` (default) means the subsystem is not wired into this deployment and will be
    reported as "unknown" — an unknown component never pulls the rollup below operational.

    Constructing StatusDeps opens nothing — no network, no DB, no boto3.
    """

    # Each optional probe: () -> "operational" | "degraded" | "down"
    data_plane: Callable[[], State] | None = None
    agent_plane: Callable[[], State] | None = None
    ingest: Callable[[], State] | None = None


# ---------------------------------------------------------------------------  rollup helpers

def _rollup(states: list[State]) -> State:
    """Compute the aggregate status from a list of per-component states.

    Rules (applied in priority order):
      * "down"        if ANY component is "down"
      * "degraded"    if ANY component is "degraded" (and none is "down")
      * "operational" otherwise — unknown components do NOT degrade the rollup.
    """
    worst = 0
    for s in states:
        rank = _SEVERITY_RANK.get(s, 0)
        if rank > worst:
            worst = rank
    if worst >= 2:
        return _DOWN
    if worst >= 1:
        return _DEGRADED
    return _OPERATIONAL


def _probe_component(key: str, label: str, probe: Callable[[], State] | None) -> dict:
    """Run a single probe and return its component dict; absorbs probe exceptions → "down"."""
    if probe is None:
        return {
            "key": key,
            "label": label,
            "state": _UNKNOWN,
            "detail": "not reporting on this deployment",
        }
    try:
        state = probe()
        return {"key": key, "label": label, "state": state, "detail": None}
    except Exception:  # noqa: BLE001 — surface probe errors as "down", not 500
        # This endpoint is PUBLIC + unauthenticated: a raw str(exc) here could leak
        # internal detail (DSN fragments, hostnames, stack text) to anyone. Log the
        # full exception server-side; return only a generic, sanitised detail.
        log.exception("status probe %r failed", key)
        return {"key": key, "label": label, "state": _DOWN,
                "detail": "probe error — see server logs"}


# ---------------------------------------------------------------------------  mount

def mount_status(
    app: FastAPI,
    deps: StatusDeps,
    *,
    checked_at: str | None = None,
) -> None:
    """Register GET /public/status and GET /api/status on *app* with no auth dependency.

    ``checked_at`` is an optional ISO timestamp injected at mount time (for testing
    determinism or when the probe wiring layer wants to stamp the time); if omitted the
    field is absent from the response.
    """

    @app.get("/public/status")
    async def get_status():
        # api is always operational — this endpoint is answering.
        components: list[dict] = [
            {"key": "api", "label": "API", "state": _OPERATIONAL, "detail": None},
        ]
        components.append(_probe_component("data_plane", "Data plane", deps.data_plane))
        components.append(_probe_component("agent_plane", "Agent plane", deps.agent_plane))
        components.append(_probe_component("ingest", "Ingest", deps.ingest))

        overall = _rollup([c["state"] for c in components])
        body: dict = {"status": overall, "components": components}
        if checked_at is not None:
            body["checked_at"] = checked_at
        return body

    # /api/status alias — identical handler, registered second so /public/status is canonical.
    @app.get("/api/status")
    async def get_status_alias():
        return await get_status()
