"""Unit: the playbook trigger dispatcher (agents/playbooks/dispatch.py) — cron matching,
ACTIVE-only firing, schedule vs event selection, and containment. Offline: fake store + runner."""
from datetime import datetime, timezone

import pytest

from agents.playbooks import STATUS_ACTIVE, STATUS_DRAFT
from agents.playbooks.dispatch import PlaybookDispatcher, cron_due
from agents.playbooks.runner import RunRecord, TriggerEvent


# --------------------------------------------------------------------------- cron_due
@pytest.mark.unit
@pytest.mark.parametrize("expr,dt,expected", [
    ("* * * * *", datetime(2026, 6, 11, 13, 30, tzinfo=timezone.utc), True),
    ("30 13 * * *", datetime(2026, 6, 11, 13, 30, tzinfo=timezone.utc), True),
    ("30 13 * * *", datetime(2026, 6, 11, 13, 31, tzinfo=timezone.utc), False),  # minute miss
    ("0 13 * * 1-5", datetime(2026, 6, 11, 13, 0, tzinfo=timezone.utc), True),   # Thu in Mon-Fri
    ("0 13 * * 1-5", datetime(2026, 6, 13, 13, 0, tzinfo=timezone.utc), False),  # Sat
    ("0 14 * * 2", datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc), True),      # Tue
    ("0 14 * * 0", datetime(2026, 6, 14, 14, 0, tzinfo=timezone.utc), True),     # Sun=0
    ("0 14 * * 7", datetime(2026, 6, 14, 14, 0, tzinfo=timezone.utc), True),     # Sun=7 normalizes to 0
    ("0 14 * * 7", datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc), False),    # Mon: dow=7 must NOT fire
    ("0 13 1 * 1", datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc), True),      # dom+dow both set -> Mon OR the 1st
    ("*/15 * * * *", datetime(2026, 6, 11, 9, 45, tzinfo=timezone.utc), True),   # step
    ("*/15 * * * *", datetime(2026, 6, 11, 9, 46, tzinfo=timezone.utc), False),
    ("0 0 1 * *", datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc), True),        # day-of-month
    ("0 0 1 * *", datetime(2026, 6, 2, 0, 0, tzinfo=timezone.utc), False),
    ("bad cron", datetime(2026, 6, 11, 13, 30, tzinfo=timezone.utc), False),     # malformed
    ("99 * * * *", datetime(2026, 6, 11, 13, 30, tzinfo=timezone.utc), False),   # out of range
])
def test_cron_due(expr, dt, expected):
    assert cron_due(expr, dt) is expected


# --------------------------------------------------------------------------- fakes
class FakeStore:
    def __init__(self, rows):
        self._rows = rows  # list of {id, status, definition}

    def list(self, tenant_id):
        return list(self._rows)


def _row(pid, status, trigger):
    return {"id": pid, "status": status, "definition": {"trigger": trigger, "name": pid}}


def _runner():
    """A run_playbook that records calls and returns an ok RunRecord."""
    calls = []

    def run_playbook(tenant_id, playbook_id, event: TriggerEvent) -> RunRecord:
        calls.append((tenant_id, playbook_id, event))
        return RunRecord(playbook_id=playbook_id, tenant_id=tenant_id, status="ok",
                         trigger={"kind": event.kind, "name": event.name})
    return run_playbook, calls


# --------------------------------------------------------------------------- dispatch_scheduled
@pytest.mark.unit
def test_scheduled_runs_only_active_due_schedule_playbooks():
    now = datetime(2026, 6, 11, 13, 0, tzinfo=timezone.utc)  # Thu 13:00
    rows = [
        _row("due", STATUS_ACTIVE, {"kind": "schedule", "schedule": "0 13 * * 1-5"}),  # fires
        _row("not_due", STATUS_ACTIVE, {"kind": "schedule", "schedule": "0 9 * * *"}),  # wrong hour
        _row("draft", STATUS_DRAFT, {"kind": "schedule", "schedule": "0 13 * * 1-5"}),  # not active
        _row("event", STATUS_ACTIVE, {"kind": "event", "event": "lead.created"}),       # not schedule
    ]
    run_playbook, calls = _runner()
    out = PlaybookDispatcher(FakeStore(rows), run_playbook).dispatch_scheduled("T1", now=now)
    assert [r.playbook_id for r in out] == ["due"]
    assert len(calls) == 1
    assert calls[0][2].kind == "schedule"


@pytest.mark.unit
def test_scheduled_empty_when_nothing_due():
    now = datetime(2026, 6, 11, 3, 0, tzinfo=timezone.utc)
    rows = [_row("p", STATUS_ACTIVE, {"kind": "schedule", "schedule": "0 13 * * *"})]
    run_playbook, calls = _runner()
    out = PlaybookDispatcher(FakeStore(rows), run_playbook).dispatch_scheduled("T1", now=now)
    assert out == [] and calls == []


# --------------------------------------------------------------------------- dispatch_event
@pytest.mark.unit
def test_event_runs_only_matching_active_event_playbooks():
    rows = [
        _row("match", STATUS_ACTIVE, {"kind": "event", "event": "lead.created"}),
        _row("other_event", STATUS_ACTIVE, {"kind": "event", "event": "deal.won"}),
        _row("draft", STATUS_DRAFT, {"kind": "event", "event": "lead.created"}),
        _row("sched", STATUS_ACTIVE, {"kind": "schedule", "schedule": "* * * * *"}),
    ]
    run_playbook, calls = _runner()
    out = PlaybookDispatcher(FakeStore(rows), run_playbook).dispatch_event(
        "T1", "lead.created", payload={"id": "L1"})
    assert [r.playbook_id for r in out] == ["match"]
    assert calls[0][2].kind == "event"
    assert calls[0][2].payload == {"id": "L1"}


@pytest.mark.unit
def test_event_no_match_runs_nothing():
    rows = [_row("p", STATUS_ACTIVE, {"kind": "event", "event": "deal.won"})]
    run_playbook, calls = _runner()
    out = PlaybookDispatcher(FakeStore(rows), run_playbook).dispatch_event("T1", "lead.created")
    assert out == [] and calls == []


@pytest.mark.unit
def test_module_is_import_safe():
    # Importing built nothing live; main is callable and the CLI requires a mode.
    from agents.playbooks import dispatch
    assert dispatch.main(["--tenant", "x"]) == 2  # no --schedule -> usage error, no DB touched


@pytest.mark.unit
def test_build_runner_realmode_imports_resolve(monkeypatch):
    """REGRESSION: the real-mode CLI path imports PgWorkspaceStore — assert it resolves from the
    correct module (agents.workspace_store, not api.pg_clients) and wires a store + runner."""
    import agents.playbooks.store as store_mod
    import agents.workspace_store as ws_mod
    import agents.runtime as rt_mod
    from agents.playbooks.dispatch import _build_runner

    monkeypatch.setattr(store_mod, "PgPlaybookStore", lambda dsn: ("store", dsn))
    monkeypatch.setattr(ws_mod, "PgWorkspaceStore", lambda dsn: object())
    monkeypatch.setattr(rt_mod, "get_runtime", lambda cfg: object())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    store, run_playbook = _build_runner("postgresql://crm_app@h/db")
    assert store == ("store", "postgresql://crm_app@h/db")
    assert callable(run_playbook)


# --------------------------------------------------------------------------- producer
# The event producer: POST /deals must call dispatch_event('deal.created', ...) with the
# VERIFIED tenant + the new record, and be fully INERT when no dispatcher is wired.
class _CreateOnlyCrm:
    """Minimal CRM stub for the create path — records the inserted deal and returns it."""

    def insert_deal(self, *, tenant_id, company_id, name, stage, amount, contact_id=None):
        return {"id": "D1", "title": name, "stage": stage, "amount": amount,
                "tenant_id": str(tenant_id), "company_id": company_id,
                "contact_id": contact_id, "created_at": None}


class _RecordingDispatcher:
    """Records dispatch_event calls so the producer wiring can be asserted."""

    def __init__(self):
        self.events: list[tuple] = []

    def dispatch_event(self, tenant_id, event_name, payload=None):
        self.events.append((tenant_id, event_name, payload))
        return []


class _BoomDispatcher:
    def dispatch_event(self, tenant_id, event_name, payload=None):
        raise RuntimeError("playbook blew up")


def _deals_app(dispatcher):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.auth import make_current_tenant
    from api.deals_routes import DealsDeps, mount_deals

    class _FakeVerifier:
        def verify(self, token):
            tenant = token.split("-")[1] if token.startswith("t-") else "A"
            return {"sub": f"sub-{tenant}", "custom:tenant_id": tenant,
                    "email": f"{tenant}@x.com"}

    class _FakeGateDeps:
        autonomy_config = executor = greenlight = killswitch = trace_store = None

    app = FastAPI()
    deps = DealsDeps(crm=_CreateOnlyCrm(), dispatcher=dispatcher)
    mount_deals(app, deps, make_current_tenant(_FakeVerifier()), gate_deps=_FakeGateDeps())
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
def test_create_deal_emits_deal_created_to_dispatcher():
    disp = _RecordingDispatcher()
    client = _deals_app(disp)
    r = client.post("/deals", json={"title": "Acme expansion", "amount": 1000},
                    headers={"Authorization": "Bearer t-A"})
    assert r.status_code == 201
    # Exactly one event, the verified tenant ('A' from the claim, NOT the body), the new record.
    assert len(disp.events) == 1
    tenant_id, event_name, payload = disp.events[0]
    assert tenant_id == "A"
    assert event_name == "deal.created"
    assert payload["deal"]["title"] == "Acme expansion"


@pytest.mark.unit
def test_create_deal_is_inert_without_a_dispatcher():
    # No dispatcher wired (the default for every test / non-asgi deps): create still
    # succeeds and nothing is fired — the producer never raises on a missing dispatcher.
    client = _deals_app(None)
    r = client.post("/deals", json={"title": "No-dispatcher deal"},
                    headers={"Authorization": "Bearer t-A"})
    assert r.status_code == 201
    assert r.json()["deal"]["title"] == "No-dispatcher deal"


@pytest.mark.unit
def test_create_deal_survives_a_dispatcher_failure():
    # A failing event playbook must NEVER fail the user-initiated create that already wrote.
    client = _deals_app(_BoomDispatcher())
    r = client.post("/deals", json={"title": "Resilient deal"},
                    headers={"Authorization": "Bearer t-A"})
    assert r.status_code == 201
    assert r.json()["deal"]["title"] == "Resilient deal"


# --------------------------------------------------------------------------- #
# The lead.created producer (audit P0-4): a new contact IS a new lead landing in
# the CRM — POST /contacts fires lead.created with the VERIFIED tenant + the new
# row, so the shipped lead_followup_drafter template (trigger event=lead.created)
# is actually fireable. Same guarded-inert/contained contract as deal.created.
# --------------------------------------------------------------------------- #
class _CreateOnlyContactsCrm:
    def insert_contact(self, *, tenant_id, name, email=None, phone=None, company_id=None):
        return {"id": "C1", "name": name, "email": email, "phone": phone,
                "company_id": company_id, "tenant_id": str(tenant_id), "created_at": None}


def _contacts_app(dispatcher):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.auth import make_current_tenant
    from api.contacts_routes import ContactsDeps, mount_contacts

    class _FakeVerifier:
        def verify(self, token):
            tenant = token.split("-")[1] if token.startswith("t-") else "A"
            return {"sub": f"sub-{tenant}", "custom:tenant_id": tenant,
                    "email": f"{tenant}@x.com"}

    app = FastAPI()
    deps = ContactsDeps(crm=_CreateOnlyContactsCrm(), dispatcher=dispatcher)
    mount_contacts(app, deps, make_current_tenant(_FakeVerifier()))
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.unit
def test_create_contact_emits_lead_created_to_dispatcher():
    disp = _RecordingDispatcher()
    client = _contacts_app(disp)
    r = client.post("/contacts", json={"name": "Maria Lopez", "email": "maria@acme.com"},
                    headers={"Authorization": "Bearer t-A"})
    assert r.status_code == 201
    assert len(disp.events) == 1
    tenant_id, event_name, payload = disp.events[0]
    assert tenant_id == "A"            # the VERIFIED claim tenant, never the body
    assert event_name == "lead.created"
    assert payload["contact"]["name"] == "Maria Lopez"


@pytest.mark.unit
def test_create_contact_is_inert_without_a_dispatcher():
    client = _contacts_app(None)
    r = client.post("/contacts", json={"name": "No-dispatcher lead"},
                    headers={"Authorization": "Bearer t-A"})
    assert r.status_code == 201
    assert r.json()["contact"]["name"] == "No-dispatcher lead"


@pytest.mark.unit
def test_create_contact_survives_a_dispatcher_failure():
    client = _contacts_app(_BoomDispatcher())
    r = client.post("/contacts", json={"name": "Resilient lead"},
                    headers={"Authorization": "Bearer t-A"})
    assert r.status_code == 201
    assert r.json()["contact"]["name"] == "Resilient lead"


# --------------------------------------------------------------------------- #
# BackgroundDispatcher: producers (POST /contacts, POST /deals) must never block
# a user request on an agent run — an MA coordinator turn can take tens of
# seconds. dispatch_event returns immediately; the run happens on a contained
# daemon thread and its result lands in the persisted run history (audit P0-2).
# --------------------------------------------------------------------------- #
def test_background_dispatcher_returns_immediately_and_still_dispatches():
    import threading
    import time

    from agents.playbooks.dispatch import BackgroundDispatcher

    started = threading.Event()
    release = threading.Event()
    calls: list[tuple] = []

    class _SlowInner:
        def dispatch_event(self, tenant_id, event_name, payload=None):
            started.set()
            release.wait(timeout=5)          # simulate a slow agent run
            calls.append((tenant_id, event_name, payload))
            return ["record"]

    bg = BackgroundDispatcher(_SlowInner())
    t0 = time.monotonic()
    out = bg.dispatch_event("t-A", "lead.created", {"contact": {"id": "C1"}})
    assert time.monotonic() - t0 < 0.5, "the producer call must not block on the run"
    assert out == []                          # fire-and-forget: nothing to report yet
    assert started.wait(timeout=2), "the background run never started"
    release.set()
    deadline = time.monotonic() + 2
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls == [("t-A", "lead.created", {"contact": {"id": "C1"}})]


def test_background_dispatcher_contains_inner_failures():
    import time

    from agents.playbooks.dispatch import BackgroundDispatcher

    class _BoomInner:
        def dispatch_event(self, tenant_id, event_name, payload=None):
            raise RuntimeError("agent plane down")

    bg = BackgroundDispatcher(_BoomInner())
    assert bg.dispatch_event("t-A", "lead.created") == []  # never raises into the request
    time.sleep(0.05)  # let the thread die — nothing to assert beyond "no crash"


# --------------------------------------------------------------------------- #
# Tenant resolution: DB discovery replaces the static tfvar list so a new signup
# gets scheduled playbooks without a terraform edit. Offline — the `discover`
# seam is injected, so these never touch a database.
# --------------------------------------------------------------------------- #
def _ns(tenant=None):
    import argparse
    return argparse.Namespace(tenant=tenant, schedule=True)


@pytest.mark.unit
def test_explicit_tenant_args_win_and_skip_discovery():
    from agents.playbooks.dispatch import _resolve_tenants

    called = []

    def discover(dsn):
        called.append(dsn)
        return ["db-tenant"]

    out = _resolve_tenants(_ns(tenant=["x", " y ", "x"]), dsn="dsn", discover=discover)
    assert out == ["x", "y"]      # deduped + trimmed, in order
    assert called == []           # explicit args short-circuit discovery entirely


@pytest.mark.unit
def test_db_discovery_is_unioned_with_the_static_env_list(monkeypatch):
    from agents.playbooks import dispatch
    from agents.playbooks.dispatch import _resolve_tenants

    monkeypatch.setenv(dispatch.ENV_DISPATCH_TENANTS, "legacy-a, legacy-b")
    out = _resolve_tenants(_ns(), dsn="dsn",
                           discover=lambda dsn: ["legacy-b", "new-signup"])  # overlap dedupes
    assert out == ["legacy-a", "legacy-b", "new-signup"]


@pytest.mark.unit
def test_db_discovery_alone_picks_up_new_signups(monkeypatch):
    from agents.playbooks import dispatch
    from agents.playbooks.dispatch import _resolve_tenants

    monkeypatch.delenv(dispatch.ENV_DISPATCH_TENANTS, raising=False)
    out = _resolve_tenants(_ns(), dsn="dsn", discover=lambda dsn: ["fresh-tenant"])
    assert out == ["fresh-tenant"]  # no tfvar entry needed


@pytest.mark.unit
def test_resolution_is_inert_with_no_env_and_no_discovery(monkeypatch):
    from agents.playbooks import dispatch
    from agents.playbooks.dispatch import _resolve_tenants

    monkeypatch.delenv(dispatch.ENV_DISPATCH_TENANTS, raising=False)
    assert _resolve_tenants(_ns(), dsn=None, discover=lambda dsn: []) == []


@pytest.mark.unit
def test_discover_db_tenants_is_inert_without_a_dsn():
    from agents.playbooks.dispatch import discover_db_tenants

    assert discover_db_tenants(None) == []  # no store -> safe no-op, no driver import attempted


# --------------------------------------------------------------------------- #
# Tick-floor (found live 2026-06-12): EventBridge fires at :00/:15/:30/:45 but
# the Fargate container starts ~30-90s later, so matching the cron against
# datetime.now() misses the tick minute EVERY time (the 15:00 tick evaluated at
# 15:01:10 -> '*/15 * * * *' never matched; "0 playbook run(s)"). main() must
# match against the tick the run belongs to: now floored to the 15-min boundary.
# --------------------------------------------------------------------------- #
def test_tick_floor_recovers_the_scheduled_minute():
    from datetime import datetime, timezone

    from agents.playbooks.dispatch import _tick_floor

    t = lambda h, m, s: datetime(2026, 6, 12, h, m, s, 123456, tzinfo=timezone.utc)  # noqa: E731
    assert _tick_floor(t(15, 1, 10)) == t(15, 0, 0).replace(microsecond=0)   # the live miss
    assert _tick_floor(t(8, 16, 8)) == t(8, 15, 0).replace(microsecond=0)    # first enabled tick
    assert _tick_floor(t(15, 14, 59)) == t(15, 0, 0).replace(microsecond=0)  # extreme jitter
    assert _tick_floor(t(15, 15, 0)) == t(15, 15, 0).replace(microsecond=0)  # exact boundary
    assert _tick_floor(t(23, 59, 59)) == t(23, 45, 0).replace(microsecond=0)


def test_quarter_hour_cron_fires_under_startup_jitter():
    """The end-to-end shape of the live failure: an ACTIVE '*/15' playbook + a dispatcher
    evaluated 70 seconds after the tick MUST still run it (via the floored tick time)."""
    from datetime import datetime, timezone

    from agents.playbooks.dispatch import PlaybookDispatcher, _tick_floor
    from agents.playbooks.runner import TriggerEvent  # noqa: F401 — signature parity
    from agents.playbooks.store import InMemoryPlaybookStore

    store = InMemoryPlaybookStore()
    row = store.create("t-1", {
        "name": "Quarter-hour scout", "trigger": {"kind": "schedule", "schedule": "*/15 * * * *"},
        "roster": [{"agent": "scout", "tools": ["read_crm"]}], "autonomy": "L1",
        "greenlight": {"side_effects": "always_ask"},
    })
    store.set_status("t-1", row["id"], "active")
    ran = []
    dispatcher = PlaybookDispatcher(store, lambda tid, pid, ev: ran.append((tid, pid)) or
                                    type("R", (), {"playbook_id": pid, "status": "ok"})())

    container_start = datetime(2026, 6, 12, 15, 1, 10, tzinfo=timezone.utc)
    dispatcher.dispatch_scheduled("t-1", now=_tick_floor(container_start))
    assert ran == [("t-1", row["id"])], "the floored tick must fire the quarter-hour cron"
