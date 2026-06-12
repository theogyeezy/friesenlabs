"""Unit: the Sell (gamification) module is COHERENT end-to-end — the catalog's promise is real.

SELL-A/B/C/D land the module across four surfaces that must agree, or the feature silently half-works:
  * the module catalog (shared/modules.py MODULES) declares a `sell` module gating route-id "sell",
  * the runtime catalog (GET /account/modules == catalog_payload) gates that route by entitlement —
    "sell" appears in `enabled_routes` ONLY when the module is enabled (so the web entitlement gate,
    not the mock gamifyOn toggle, controls visibility in real mode),
  * the real app factory (api.app.create_app → api.sell_routes.mount_sell) actually MOUNTS the
    /sell/* endpoints that route-id stands for — the catalog never promises a dangling route,
  * the web TS mirror (web/src/modules.ts) lists the same `sell` entry (full parity is asserted by
    test_modules_parity; here we anchor the sell row specifically as the finalize guarantee).

This is the WAVE-E handoff check: it fails loudly if a future change drops the module, renames the
route, removes the mount, or lets the catalog and the mounted routes drift apart.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig, Thresholds
from api.control.greenlight import Greenlight, InMemoryApprovalStore
from api.control.killswitch import KillSwitch
from api.control.types import Level
from api.gamify_stores import InMemoryMemberStore, InMemoryPointsStore
from api.views import SavedViews
from shared import modules as M

_MODULES_TS = Path(__file__).resolve().parents[2] / "web" / "src" / "modules.ts"

H = {"Authorization": "Bearer t"}


class _FakeVerifier:
    def verify(self, token):
        return {"sub": "u1", "custom:tenant_id": "A", "name": "Alice", "email": "a@x.com"}


def _build_app(*, points=None, members=None):
    """The REAL app factory with in-memory fakes (mirrors test_sell_routes) — so the mounted route
    set is the production one, not a hand-rolled router."""
    deps = ApiDeps(
        verifier=_FakeVerifier(),
        greenlight=Greenlight(store=InMemoryApprovalStore()),
        saved_views=SavedViews(),
        conversation_factory=lambda t: None,
        autonomy_config=AutonomyConfig(default_level=Level.L1,
                                       thresholds=Thresholds(max_auto_value=1000)),
        executor=lambda a: {"ran": True},
        killswitch=KillSwitch(),
        members=members,
        points=points,
    )
    return create_app(deps)


# ---- catalog: the module exists and gates the route it claims --------------------------------

def test_sell_module_in_catalog_gates_route_sell():
    sell = M.get_module("sell")
    assert sell is not None, "the 'sell' module must exist in shared/modules.py MODULES"
    assert sell.routes == ("sell",), "the sell module must gate exactly route-id 'sell'"
    assert not sell.required, "sell is an opt-in à-la-carte module, never force-on"


def test_sell_route_gated_by_entitlement_not_always_on():
    # Enabled -> the route-id is visible; disabled -> it is NOT (the entitlement gate has teeth).
    assert "sell" in M.enabled_routes({"command", "sell"})
    assert "sell" not in M.enabled_routes({"command"})
    # And it is not one of the always-on (ungated) surfaces.
    assert "sell" not in M.ALWAYS_ON_ROUTES


def test_catalog_payload_exposes_sell_enabled_flag_and_route():
    on = M.catalog_payload({"command", "sell"})
    assert any(m["id"] == "sell" and m["enabled"] for m in on["modules"])
    assert "sell" in on["enabled_routes"]

    off = M.catalog_payload({"command"})
    assert any(m["id"] == "sell" and not m["enabled"] for m in off["modules"])
    assert "sell" not in off["enabled_routes"]


# ---- mounted routes: the catalog's promised route-id is backed by real endpoints --------------

def test_create_app_mounts_the_sell_endpoints():
    app = _build_app(points=InMemoryPointsStore(), members=InMemoryMemberStore())
    paths = {getattr(r, "path", None) for r in app.routes}
    for p in ("/sell/me", "/sell/leaderboard", "/sell/quests", "/sell/nudge"):
        assert p in paths, f"create_app must mount {p} (mount_sell wired in api/app.py)"
        assert f"/api{p}" in paths, f"the /api alias for {p} must also be mounted"


def test_sell_reads_are_inert_503_without_points_store():
    # The finalize honesty contract: mounted, but a 503 (never fabricated data) when no DSN/store.
    client = TestClient(_build_app(points=None))
    for p in ("/sell/me", "/sell/leaderboard", "/sell/quests"):
        assert client.get(p, headers=H).status_code == 503


def test_sell_reads_answer_when_points_store_wired():
    # With the store wired (asgi.py does this from the crm_app DSN), the same reads answer 200.
    client = TestClient(_build_app(points=InMemoryPointsStore(), members=InMemoryMemberStore()))
    for p in ("/sell/me", "/sell/leaderboard", "/sell/quests"):
        assert client.get(p, headers=H).status_code == 200


# ---- web mirror: the sell row is present on the TS side too (anchor for the parity test) -------

def test_web_modules_ts_lists_sell():
    src = _MODULES_TS.read_text(encoding="utf-8")
    body = src.split("export const MODULES", 1)[1].split("];", 1)[0]
    m = re.search(
        r"\{\s*id:\s*\"sell\",\s*name:\s*\"[^\"]+\",\s*monthlyCents:\s*\d+,\s*"
        r"required:\s*false,\s*routes:\s*\[(?P<routes>[^\]]*)\]\s*\}",
        body,
    )
    assert m is not None, "web/src/modules.ts MODULES must contain the 'sell' module entry"
    routes = re.findall(r"\"([^\"]+)\"", m["routes"])
    assert routes == ["sell"], "the web 'sell' module must gate exactly route 'sell'"
