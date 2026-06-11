"""Unit: the module catalog (shared/modules.py) — normalization, route gating, totals."""
import pytest

from shared import modules as M


@pytest.mark.unit
def test_required_command_is_always_on():
    assert "command" in M.REQUIRED_IDS
    # normalize forces required modules on even if the caller drops them.
    assert "command" in M.normalize_enabled([])
    assert "command" in M.normalize_enabled(["cortex"])


@pytest.mark.unit
def test_valid_module_ids_drops_unknowns():
    assert M.valid_module_ids(["cortex", "bogus", "uplift"]) == {"cortex", "uplift"}


@pytest.mark.unit
def test_default_enabled_is_full_suite():
    # Opt-out model: a tenant with no entitlements row sees everything (and the
    # pre-migrate / store-error fallback never strands a tenant out of a surface).
    assert M.default_enabled() == set(M.MODULE_IDS)
    assert set(M.REQUIRED_IDS) <= M.default_enabled()


@pytest.mark.unit
def test_enabled_routes_gates_correctly():
    routes = M.enabled_routes(["uplift", "cortex"])
    # uplift's + cortex's routes are visible...
    assert {"crm", "contacts", "cortex"} <= routes
    # ...always-on routes are always visible...
    assert {"settings", "security"} <= routes
    # ...command (required) is forced on, so its routes show too...
    assert "dashboard" in routes
    # ...but a NOT-enabled module's route is hidden.
    assert "workflows" not in routes
    assert "knowledge" not in routes


@pytest.mark.unit
def test_monthly_total_sums_enabled_plus_required():
    # command (4900, required) + cortex (4500)
    assert M.monthly_total_cents(["cortex"]) == 4900 + 4500
    # unknown ids don't add
    assert M.monthly_total_cents(["cortex", "bogus"]) == 4900 + 4500


@pytest.mark.unit
def test_catalog_payload_shape():
    payload = M.catalog_payload(["cortex"])
    assert payload["monthly_total_cents"] == 4900 + 4500
    by_id = {m["id"]: m for m in payload["modules"]}
    assert by_id["command"]["required"] is True and by_id["command"]["enabled"] is True
    assert by_id["cortex"]["enabled"] is True
    assert by_id["workflows"]["enabled"] is False
    # every catalog field present
    for m in payload["modules"]:
        assert set(m) == {"id", "name", "monthly_cents", "required", "enabled"}
    # enabled_routes lets the web gate without mirroring the route map
    assert "cortex" in payload["enabled_routes"]
    assert "settings" in payload["enabled_routes"]   # always-on
    assert "workflows" not in payload["enabled_routes"]


@pytest.mark.unit
def test_get_module_and_ids():
    assert M.get_module("cortex").name == "Cortex"
    assert M.get_module("bogus") is None
    assert "command" in M.MODULE_IDS and len(M.MODULE_IDS) == len(M.MODULES)
