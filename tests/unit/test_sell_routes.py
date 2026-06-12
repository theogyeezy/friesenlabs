"""Unit: the Sell (gamification) routes — GET /sell/me · /sell/leaderboard · /sell/quests · POST
/sell/nudge.

Drives the REAL app factory (api.app.create_app) with in-memory fakes. Proven here:
  * every surface is claims-bound — tenant + user come from the verified JWT only (THE TRUST RULE)
  * /sell/me derives level/xp/streak/today-progress from the points ledger + the display rules
  * /sell/leaderboard is tenant-scoped, strips the internal tenant_id, and a leaky store row trips
    the defense-in-depth re-check (mirror /views) as a 500 rather than leaking
  * /sell/quests derives one honest close-based quest from the ledger
  * /sell/nudge NEVER sends — it routes through the existing Greenlight gate as a draft-only proposal
  * inert-by-default: with no points store wired every read answers an honest 503
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig, Thresholds
from api.control.greenlight import Greenlight, InMemoryApprovalStore
from api.control.killswitch import KillSwitch
from api.control.types import Level
from api.gamify_stores import InMemoryMemberStore, InMemoryPointsStore
from api.views import SavedViews
from shared.gamify_rules import DEAL_CLOSED_WON

H = {"Authorization": "Bearer t"}


class FakeVerifier:
    def __init__(self, sub="u1", tenant="A"):
        self.sub, self.tenant = sub, tenant

    def verify(self, token):
        return {"sub": self.sub, "custom:tenant_id": self.tenant, "name": "Alice", "email": "a@x.com"}


def _deps(*, points=None, members=None, verifier=None, greenlight=None):
    return ApiDeps(
        verifier=verifier or FakeVerifier(),
        greenlight=greenlight or Greenlight(store=InMemoryApprovalStore()),
        saved_views=SavedViews(),
        conversation_factory=lambda t: None,
        autonomy_config=AutonomyConfig(default_level=Level.L1,
                                       thresholds=Thresholds(max_auto_value=1000)),
        executor=lambda a: {"ran": True},
        killswitch=KillSwitch(),
        members=members,
        points=points,
    )


def _iso(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _today():
    return datetime.now(timezone.utc).date().isoformat()


# --------------------------------------------------------------------------- #
# GET /sell/me
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_me_derives_level_xp_streak_and_today():
    points = InMemoryPointsStore()
    # 2 closes today + 1 yesterday => 30 xp, a 2-day streak, today=20 over 2 events.
    for d in (0, 0, 1):
        points.append({"tenant_id": "A", "user_id": "u1", "event_type": DEAL_CLOSED_WON,
                       "points": 10, "occurred_at": _iso(d)})
    client = TestClient(create_app(_deps(points=points)))

    body = client.get("/sell/me", headers=H).json()
    assert body["user_id"] == "u1"
    assert body["xp"] == 30 and body["events"] == 3
    assert body["level"] == 1                      # 30 < 100 xp/level
    assert body["streak"] == 2                      # today + yesterday
    assert body["today"] == {"points": 20, "events": 2}
    assert body["progress"]["into_level"] == 30 and body["progress"]["next_level_xp"] == 100


@pytest.mark.unit
def test_me_zero_for_a_rep_with_no_events():
    client = TestClient(create_app(_deps(points=InMemoryPointsStore())))
    body = client.get("/sell/me", headers=H).json()
    assert body == {
        "user_id": "u1", "level": 1, "xp": 0, "events": 0, "streak": 0,
        "today": {"points": 0, "events": 0},
        "progress": {"level": 1, "xp": 0, "into_level": 0, "span": 100,
                     "to_next": 100, "next_level_xp": 100, "pct": 0.0},
    }


@pytest.mark.unit
def test_me_is_claims_bound_only_sees_own_tenant():
    points = InMemoryPointsStore()
    points.append({"tenant_id": "B", "user_id": "u1", "event_type": DEAL_CLOSED_WON,
                   "points": 99, "occurred_at": _iso(0)})
    # The caller's verified tenant is A — the same user_id in tenant B must be invisible.
    client = TestClient(create_app(_deps(points=points, verifier=FakeVerifier(tenant="A"))))
    body = client.get("/sell/me", headers=H).json()
    assert body["xp"] == 0 and body["events"] == 0


# --------------------------------------------------------------------------- #
# GET /sell/leaderboard
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_leaderboard_is_tenant_scoped_and_strips_internal_tenant_id():
    members = InMemoryMemberStore()
    members.upsert("A", "u1", display_name="Alice")
    points = InMemoryPointsStore(members=members)
    points.append({"tenant_id": "A", "user_id": "u1", "points": 10})
    points.append({"tenant_id": "A", "user_id": "u2", "points": 4})
    points.append({"tenant_id": "B", "user_id": "u1", "points": 99})  # foreign tenant
    client = TestClient(create_app(_deps(points=points, members=members)))

    board = client.get("/sell/leaderboard", headers=H).json()["leaderboard"]
    assert [r["user_id"] for r in board] == ["u1", "u2"]   # highest first, no B leak
    assert board[0]["display_name"] == "Alice" and board[0]["points"] == 10
    assert all("tenant_id" not in r for r in board)        # internal id stripped


@pytest.mark.unit
def test_leaderboard_recheck_trips_on_a_leaky_store():
    class LeakyPoints(InMemoryPointsStore):
        def leaderboard_rows(self, tenant_id, since=None):
            # Simulate an RLS leak: a foreign-tenant row reaches the route.
            return [{"tenant_id": "OTHER", "user_id": "x", "display_name": None,
                     "points": 1, "events": 1}]

    client = TestClient(create_app(_deps(points=LeakyPoints())))
    assert client.get("/sell/leaderboard", headers=H).status_code == 500


# --------------------------------------------------------------------------- #
# GET /sell/quests
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_quests_derives_one_close_based_quest_from_the_ledger():
    points = InMemoryPointsStore()
    for _ in range(2):
        points.append({"tenant_id": "A", "user_id": "u1", "event_type": DEAL_CLOSED_WON,
                       "points": 10, "occurred_at": _iso(0)})
    client = TestClient(create_app(_deps(points=points)))

    quests = client.get("/sell/quests", headers=H).json()["quests"]
    assert len(quests) == 1
    q = quests[0]
    assert q["event_type"] == DEAL_CLOSED_WON
    assert q["current"] == 2                 # two real closes in the window
    assert q["progress"] == 2 and q["target"] >= 2
    assert q["complete"] is False


# --------------------------------------------------------------------------- #
# POST /sell/nudge — draft-only, never a direct send
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_nudge_routes_through_greenlight_draft_only_never_sends():
    greenlight = Greenlight(store=InMemoryApprovalStore())
    points = InMemoryPointsStore()
    client = TestClient(create_app(_deps(points=points, greenlight=greenlight)))

    r = client.post("/sell/nudge", headers=H,
                    json={"user_id": "teammate@x.com", "message": "You're 1 close from level 2!"})
    assert r.status_code == 200
    out = r.json()
    assert out["status"] == "queued" and out["draft_only"] is True
    assert out["approval_id"]

    # The nudge created a PENDING draft proposal — nothing was sent.
    pending = greenlight.list_pending("A")
    assert len(pending) == 1
    proposed = pending[0]["proposed_action"]
    assert proposed["action"] == "send_email"          # the existing draft-only outbound tool
    assert proposed["body"] == "You're 1 close from level 2!"
    assert pending[0]["status"] == "pending"           # awaits human approval; never auto-sent
    assert pending[0]["agent"] == "u1"                 # the acting user, from the verified JWT


@pytest.mark.unit
def test_nudge_tenant_and_actor_come_from_the_jwt_only():
    greenlight = Greenlight(store=InMemoryApprovalStore())
    client = TestClient(create_app(_deps(points=InMemoryPointsStore(), greenlight=greenlight,
                                         verifier=FakeVerifier(sub="boss", tenant="B"))))
    # A forged tenant_id in the body must be ignored — the draft lands in the JWT's tenant.
    client.post("/sell/nudge", headers=H,
                json={"user_id": "rep@x.com", "message": "go", "tenant_id": "A"})
    assert greenlight.list_pending("A") == []          # never the body's tenant
    assert len(greenlight.list_pending("B")) == 1      # always the verified tenant
    assert greenlight.list_pending("B")[0]["agent"] == "boss"


# --------------------------------------------------------------------------- #
# inert-by-default: honest 503 when the points store isn't wired
# --------------------------------------------------------------------------- #
@pytest.mark.unit
@pytest.mark.parametrize("path", ["/sell/me", "/sell/leaderboard", "/sell/quests"])
def test_reads_are_503_when_points_store_unwired(path):
    client = TestClient(create_app(_deps(points=None)))
    assert client.get(path, headers=H).status_code == 503
