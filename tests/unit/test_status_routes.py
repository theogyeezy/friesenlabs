"""Unit: GET /public/status + GET /api/status — public readiness endpoint (api/status_routes.py).

Mounts ``mount_status`` on a bare FastAPI app with injected probe stubs; zero DB, zero AWS.
Covers:

  * unauth access works (200, no bearer required)
  * the ``api`` component is always "operational"
  * no probes → subsystems "unknown" but overall is "operational" (not "degraded")
  * an injected probe returning "degraded" → overall "degraded"
  * an injected probe returning "down" → overall "down"
  * a probe that raises → that component "down" with error detail (not a 500)
  * the unknown-doesn't-degrade rollup invariant (api=operational + unknowns = "operational")
  * /api/status alias returns identical payload
  * checked_at is included in the response when supplied at mount time
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.status_routes import StatusDeps, mount_status


def _client(deps: StatusDeps | None = None, *, checked_at: str | None = None) -> TestClient:
    """Build a TestClient with only the status routes mounted."""
    if deps is None:
        deps = StatusDeps()
    app = FastAPI()
    mount_status(app, deps, checked_at=checked_at)
    return TestClient(app)


# ---------------------------------------------------------------------------  access / basics

@pytest.mark.unit
def test_status_endpoint_is_200_without_auth():
    """No Authorization header required — this is a public endpoint."""
    client = _client()
    r = client.get("/public/status")
    assert r.status_code == 200


@pytest.mark.unit
def test_response_has_required_top_level_keys():
    client = _client()
    body = client.get("/public/status").json()
    assert "status" in body
    assert "components" in body
    assert isinstance(body["components"], list)


@pytest.mark.unit
def test_api_component_is_always_operational():
    """The api component must be 'operational' regardless of probe wiring."""
    client = _client()
    body = client.get("/public/status").json()
    api_comp = next(c for c in body["components"] if c["key"] == "api")
    assert api_comp["state"] == "operational"


# ---------------------------------------------------------------------------  no-probes baseline

@pytest.mark.unit
def test_no_probes_subsystems_are_unknown():
    """With all probes None the three subsystems report 'unknown', not 'degraded' / 'down'."""
    client = _client(StatusDeps())
    body = client.get("/public/status").json()
    component_by_key = {c["key"]: c for c in body["components"]}
    for key in ("data_plane", "agent_plane", "ingest"):
        assert component_by_key[key]["state"] == "unknown", key


@pytest.mark.unit
def test_no_probes_overall_is_operational():
    """api=operational + all unknowns → overall 'operational', not 'degraded' (the rollup bug fix).

    This is the key invariant: an 'unknown' subsystem never pulls the rollup below operational.
    A healthy API with un-wired subsystems should show 'operational', not alarm the status page.
    """
    client = _client(StatusDeps())
    body = client.get("/public/status").json()
    assert body["status"] == "operational"


@pytest.mark.unit
def test_unknown_component_detail_is_honest():
    """An un-wired subsystem reports an honest 'not reporting on this deployment' detail."""
    client = _client(StatusDeps())
    body = client.get("/public/status").json()
    component_by_key = {c["key"]: c for c in body["components"]}
    dp = component_by_key["data_plane"]
    assert dp["state"] == "unknown"
    assert "not reporting" in (dp.get("detail") or "")


# ---------------------------------------------------------------------------  degraded probe

@pytest.mark.unit
def test_degraded_probe_makes_overall_degraded():
    """A single 'degraded' probe flips the rollup to 'degraded'."""
    deps = StatusDeps(data_plane=lambda: "degraded")
    client = _client(deps)
    body = client.get("/public/status").json()
    assert body["status"] == "degraded"


@pytest.mark.unit
def test_degraded_probe_component_state():
    deps = StatusDeps(agent_plane=lambda: "degraded")
    body = _client(deps).get("/public/status").json()
    ap = next(c for c in body["components"] if c["key"] == "agent_plane")
    assert ap["state"] == "degraded"


# ---------------------------------------------------------------------------  down probe

@pytest.mark.unit
def test_down_probe_makes_overall_down():
    """A single 'down' probe flips the rollup to 'down'."""
    deps = StatusDeps(ingest=lambda: "down")
    client = _client(deps)
    body = client.get("/public/status").json()
    assert body["status"] == "down"


@pytest.mark.unit
def test_down_beats_degraded_in_rollup():
    """'down' always wins over 'degraded' — the worst state wins."""
    deps = StatusDeps(
        data_plane=lambda: "degraded",
        agent_plane=lambda: "down",
    )
    body = _client(deps).get("/public/status").json()
    assert body["status"] == "down"


# ---------------------------------------------------------------------------  raising probe

@pytest.mark.unit
def test_raising_probe_reports_down_not_500():
    """A probe that raises must report that component as 'down' — never a 500."""
    def _boom():
        raise RuntimeError("connection refused")

    deps = StatusDeps(data_plane=_boom)
    client = _client(deps)
    r = client.get("/public/status")
    assert r.status_code == 200   # not a 500
    body = r.json()
    dp = next(c for c in body["components"] if c["key"] == "data_plane")
    assert dp["state"] == "down"


@pytest.mark.unit
def test_raising_probe_detail_is_sanitised_no_leak():
    """SECURITY: this endpoint is PUBLIC + unauth — a probe exception must NOT leak its
    raw message (DSN fragments, hostnames, stack text). The component reports 'down' with
    a generic, sanitised detail; the raw exception string never reaches the response."""
    secret = "host=internal-db.prod timed out after 5s"

    def _boom():
        raise ConnectionError(secret)

    deps = StatusDeps(ingest=_boom)
    body = _client(deps).get("/public/status").json()
    ingest_comp = next(c for c in body["components"] if c["key"] == "ingest")
    assert ingest_comp["state"] == "down"
    detail = ingest_comp.get("detail") or ""
    assert "internal-db.prod" not in detail   # the raw message must not leak
    assert "timed out" not in detail
    assert "see server logs" in detail        # the honest, generic placeholder


@pytest.mark.unit
def test_raising_probe_flips_overall_to_down():
    """A raising probe counts as 'down' for the rollup."""
    def _boom():
        raise Exception("db gone")

    deps = StatusDeps(data_plane=_boom)
    body = _client(deps).get("/public/status").json()
    assert body["status"] == "down"


# ---------------------------------------------------------------------------  rollup invariant

@pytest.mark.unit
def test_unknown_does_not_degrade_rollup_with_operational_api():
    """The core invariant: api operational + all other subsystems unknown = overall operational."""
    # Only the api component is always set; no probes = all subsystems unknown.
    body = _client(StatusDeps()).get("/public/status").json()
    assert body["status"] == "operational"


@pytest.mark.unit
def test_partial_operational_and_unknowns_stays_operational():
    """One operational probe + unknown others = still 'operational'."""
    deps = StatusDeps(data_plane=lambda: "operational")
    body = _client(deps).get("/public/status").json()
    assert body["status"] == "operational"


@pytest.mark.unit
def test_degraded_does_not_become_down_when_other_is_unknown():
    """degraded + unknown = 'degraded' (not 'down') — unknown stays neutral."""
    deps = StatusDeps(data_plane=lambda: "degraded")  # agent_plane + ingest = unknown
    body = _client(deps).get("/public/status").json()
    assert body["status"] == "degraded"


# ---------------------------------------------------------------------------  /api/status alias

@pytest.mark.unit
def test_api_status_alias_returns_200():
    """/api/status alias is accessible."""
    r = _client().get("/api/status")
    assert r.status_code == 200


@pytest.mark.unit
def test_api_status_alias_matches_public_status():
    """/api/status returns the same payload shape as /public/status."""
    deps = StatusDeps(data_plane=lambda: "degraded")
    client = _client(deps)
    pub = client.get("/public/status").json()
    alias = client.get("/api/status").json()
    # Both must agree on status and component keys.
    assert alias["status"] == pub["status"]
    pub_keys = {c["key"] for c in pub["components"]}
    alias_keys = {c["key"] for c in alias["components"]}
    assert alias_keys == pub_keys


# ---------------------------------------------------------------------------  checked_at

@pytest.mark.unit
def test_checked_at_included_when_supplied():
    """checked_at appears in the response when injected at mount time."""
    ts = "2026-06-11T12:00:00Z"
    client = _client(checked_at=ts)
    body = client.get("/public/status").json()
    assert body.get("checked_at") == ts


@pytest.mark.unit
def test_checked_at_absent_when_not_supplied():
    """checked_at is omitted (not null) when not supplied."""
    body = _client().get("/public/status").json()
    assert "checked_at" not in body


# ---------------------------------------------------------------------------  import safety

@pytest.mark.unit
def test_status_deps_construction_opens_nothing():
    """StatusDeps() must complete without touching DB, network, boto3, or psycopg2."""
    deps = StatusDeps()
    assert deps.data_plane is None
    assert deps.agent_plane is None
    assert deps.ingest is None


@pytest.mark.unit
def test_all_components_have_required_keys():
    """Every component dict must have key, label, state (detail may be None)."""
    body = _client(StatusDeps()).get("/public/status").json()
    for comp in body["components"]:
        assert "key" in comp
        assert "label" in comp
        assert "state" in comp
        assert "detail" in comp
