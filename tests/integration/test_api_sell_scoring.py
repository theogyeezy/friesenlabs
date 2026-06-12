"""Integration: Sell (gamification) close-scoring — the closed_won credit on the in-app close path.

Drives the REAL app factory. The board stage move to closed_won goes through the EXISTING
Greenlight gate (update_deal is ALWAYS_ASK), so the deal only actually lands closed_won when a human
approves. At THAT moment the approval-decide path credits the INITIATING user (the approval's
`agent` — stamped from the verified JWT sub at move-stage time) with deal.closed_won points.

Proven here:
  * an in-app close (move-stage -> approve) writes EXACTLY ONE ledger credit, to the initiator,
    for the right deal/points/event — and the deal write still happens
  * the credit goes to the INITIATOR, never the approver (decided_by)
  * a non-closed_won move scores nothing (only the closed_won transition credits)
  * close-scoring is GUARDED: the approve still succeeds (and the CRM write still happens) when
    no points store is wired, and when the points store throws
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig, Thresholds
from api.control.greenlight import Greenlight, InMemoryApprovalStore
from api.control.killswitch import KillSwitch
from api.control.types import Level
from api.deals_routes import DealsDeps
from api.gamify_stores import InMemoryPointsStore
from api.views import SavedViews
from shared.gamify_rules import DEAL_CLOSED_WON, points_for

DEAL_A1 = "11111111-1111-1111-1111-111111111111"
H = {"Authorization": "Bearer t"}


class FakeVerifier:
    def __init__(self, sub="u-mover", tenant="A"):
        self.sub, self.tenant = sub, tenant

    def verify(self, token):
        return {"sub": self.sub, "custom:tenant_id": self.tenant, "email": "a@x.com"}


class SpyCrm:
    """A PgCrmClient-shaped double with BOTH the move-stage reader (get_deal_board) and the
    applier write (update_deal_fields), recording every write so the close is provable."""

    def __init__(self, stage="negotiation"):
        self.writes: list[dict] = []
        self._row = {"id": DEAL_A1, "tenant_id": "A", "title": "Birchwood expansion",
                     "stage": stage, "amount": 84000.0, "company_name": "Birchwood"}

    def get_deal_board(self, *, tenant_id, deal_id):
        if tenant_id == self._row["tenant_id"] and deal_id == self._row["id"]:
            return dict(self._row)
        return None

    def update_deal_fields(self, *, tenant_id, deal_id, changes):
        self.writes.append({"tenant_id": tenant_id, "deal_id": deal_id, "changes": dict(changes)})
        return {"id": deal_id, "updated": dict(changes)}


class BoomPoints(InMemoryPointsStore):
    def append(self, row):
        raise RuntimeError("ledger is down")


def _deps(*, crm=None, points=None, verifier=None):
    crm = crm if crm is not None else SpyCrm()
    return ApiDeps(
        verifier=verifier or FakeVerifier(),
        greenlight=Greenlight(store=InMemoryApprovalStore()),
        saved_views=SavedViews(),
        conversation_factory=lambda t: None,
        autonomy_config=AutonomyConfig(default_level=Level.L1,
                                       thresholds=Thresholds(max_auto_value=1000)),
        executor=lambda a: {"ran": True},
        killswitch=KillSwitch(),
        crm=crm,
        deals=DealsDeps(crm=crm),
        points=points,
    )


def _approve(client, approval_id):
    return client.post(f"/approvals/{approval_id}/decide",
                       json={"decision": "approve"}, headers=H)


# --------------------------------------------------------------------------- #
# the happy path: an in-app close credits the initiator exactly once
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_inapp_close_credits_initiator_exactly_once():
    points = InMemoryPointsStore()
    crm = SpyCrm()
    client = TestClient(create_app(_deps(crm=crm, points=points)))

    # 1) The board move to closed_won lands a Greenlight proposal (deal NOT yet moved).
    r = client.post(f"/deals/{DEAL_A1}/move-stage",
                    json={"to_stage": "closed_won"}, headers=H)
    assert r.status_code == 200 and r.json()["queued"] is True
    approval_id = r.json()["approval_id"]
    assert crm.writes == []          # nothing written yet
    assert points.rows == []         # nothing scored yet

    # 2) A human approves -> the deal IS written closed_won AND the initiator is credited once.
    assert _approve(client, approval_id).status_code == 200
    assert len(crm.writes) == 1 and crm.writes[0]["changes"] == {"stage": "closed_won"}

    assert len(points.rows) == 1
    credit = points.rows[0]
    assert credit["user_id"] == "u-mover"          # the JWT sub that initiated the move
    assert credit["tenant_id"] == "A"
    assert credit["event_type"] == DEAL_CLOSED_WON
    assert credit["points"] == points_for(DEAL_CLOSED_WON)
    assert credit["deal_id"] == DEAL_A1

    # The leaderboard reflects exactly that one credit for the initiator.
    board = points.leaderboard("A")
    assert board == [{"user_id": "u-mover", "display_name": None,
                      "points": points_for(DEAL_CLOSED_WON), "events": 1}]


# --------------------------------------------------------------------------- #
# the credit goes to the INITIATOR, never the approver
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_credit_goes_to_initiator_not_approver():
    points = InMemoryPointsStore()
    crm = SpyCrm()
    # The approver (the request that decides) is a DIFFERENT user than the initiator.
    deps = _deps(crm=crm, points=points, verifier=FakeVerifier(sub="u-approver"))
    client = TestClient(create_app(deps))

    # Seed the approval as if "u-initiator" proposed the closed_won move (move-stage stamps
    # agent=claims.sub; here we seed it directly to isolate the initiator-vs-approver question).
    rec = deps.greenlight.propose(
        tenant_id="A", action="update_deal", agent="u-initiator",
        reasoning="close it", value_at_stake=84000.0,
        payload={"deal_id": DEAL_A1, "changes": {"stage": "closed_won"}},
    )

    assert _approve(client, rec["id"]).status_code == 200
    assert len(points.rows) == 1
    assert points.rows[0]["user_id"] == "u-initiator"   # NOT "u-approver"


# --------------------------------------------------------------------------- #
# only the closed_won transition scores
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_non_closed_won_move_scores_nothing():
    points = InMemoryPointsStore()
    crm = SpyCrm()
    client = TestClient(create_app(_deps(crm=crm, points=points)))

    r = client.post(f"/deals/{DEAL_A1}/move-stage",
                    json={"to_stage": "qualified"}, headers=H)
    approval_id = r.json()["approval_id"]
    assert _approve(client, approval_id).status_code == 200

    assert len(crm.writes) == 1 and crm.writes[0]["changes"] == {"stage": "qualified"}
    assert points.rows == []   # a non-closed_won move credits nothing


# --------------------------------------------------------------------------- #
# guarded: the close NEVER fails / changes when scoring is unwired or throws
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_close_succeeds_when_scoring_unwired():
    crm = SpyCrm()
    client = TestClient(create_app(_deps(crm=crm, points=None)))   # no points store

    r = client.post(f"/deals/{DEAL_A1}/move-stage",
                    json={"to_stage": "closed_won"}, headers=H)
    assert _approve(client, r.json()["approval_id"]).status_code == 200
    # The deal still closed — scoring being unwired changed nothing.
    assert len(crm.writes) == 1 and crm.writes[0]["changes"] == {"stage": "closed_won"}


@pytest.mark.integration
def test_close_succeeds_when_scoring_throws():
    crm = SpyCrm()
    client = TestClient(create_app(_deps(crm=crm, points=BoomPoints())))

    r = client.post(f"/deals/{DEAL_A1}/move-stage",
                    json={"to_stage": "closed_won"}, headers=H)
    resp = _approve(client, r.json()["approval_id"])
    # A scoring blow-up is swallowed: the approve succeeds and the deal still closed.
    assert resp.status_code == 200
    assert len(crm.writes) == 1 and crm.writes[0]["changes"] == {"stage": "closed_won"}
