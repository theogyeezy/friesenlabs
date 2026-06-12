"""Unit: the web module catalog (web/src/modules.ts) MUST mirror shared/modules.py exactly.

The running app gates off the runtime catalog (GET /account/modules), but web/src/modules.ts is a
hand-maintained TS mirror of the same source of truth. This test fails loudly if the two drift —
every id, name, monthly price, required flag, and the route-ids each module gates must match. Add a
module (or change a price/route) in ONE file and forget the other, and this goes red.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from shared import modules as M

_MODULES_TS = Path(__file__).resolve().parents[2] / "web" / "src" / "modules.ts"

# One `{ id: "x", name: "Y", monthlyCents: N, required: bool, routes: [...] }` object literal.
_OBJ = re.compile(
    r"\{\s*id:\s*\"(?P<id>[^\"]+)\",\s*"
    r"name:\s*\"(?P<name>[^\"]+)\",\s*"
    r"monthlyCents:\s*(?P<cents>\d+),\s*"
    r"required:\s*(?P<required>true|false),\s*"
    r"routes:\s*\[(?P<routes>[^\]]*)\]\s*\}"
)
_ROUTE = re.compile(r"\"([^\"]+)\"")


def _parse_ts() -> dict[str, dict]:
    """{id -> {name, monthly_cents, required, routes}} parsed from the MODULES array in modules.ts."""
    src = _MODULES_TS.read_text(encoding="utf-8")
    # Scope to the MODULES array so the ModuleDef interface / other literals never match.
    body = src.split("export const MODULES", 1)[1]
    body = body.split("];", 1)[0]
    out: dict[str, dict] = {}
    for m in _OBJ.finditer(body):
        out[m["id"]] = {
            "name": m["name"],
            "monthly_cents": int(m["cents"]),
            "required": m["required"] == "true",
            "routes": tuple(_ROUTE.findall(m["routes"])),
        }
    return out


@pytest.mark.unit
def test_modules_ts_file_exists():
    assert _MODULES_TS.exists(), f"missing TS mirror: {_MODULES_TS}"


@pytest.mark.unit
def test_module_ids_match():
    ts = _parse_ts()
    assert set(ts) == set(M.MODULE_IDS)


@pytest.mark.unit
def test_sell_is_registered_both_sides():
    # WAVE D: the Sell gamification surface is a real module on both sides.
    assert "sell" in M.MODULE_IDS
    sell = M.get_module("sell")
    assert sell is not None and "sell" in sell.routes
    assert "sell" in _parse_ts()


@pytest.mark.unit
def test_each_module_field_matches():
    ts = _parse_ts()
    for mod in M.MODULES:
        t = ts[mod.id]
        assert t["name"] == mod.name, mod.id
        assert t["monthly_cents"] == mod.monthly_cents, mod.id
        assert t["required"] == mod.required, mod.id
        # routes are the same SET (order in the TS mirror is incidental).
        assert set(t["routes"]) == set(mod.routes), mod.id


@pytest.mark.unit
def test_no_extra_modules_in_ts():
    # A module present in the TS mirror but not the Python catalog is just as bad as the reverse.
    assert set(_parse_ts()) - set(M.MODULE_IDS) == set()
