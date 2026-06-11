"""Integration: /public/leads rate-limit keys on the TRUST-BOUNDARY viewer IP, not the ALB peer.

The fix (api/public_routes._trusted_client_ip): behind CloudFront -> ALB -> Fargate the socket
peer is the load balancer, shared by every viewer — keying the limiter on it lets one attacker
drain the whole quota. We parse X-Forwarded-For and take the entry `trusted_hops` from the right
(default 2 = CloudFront stamps the viewer, ALB stamps CloudFront).

These tests exercise the parser directly (deterministic, no ASGI socket-peer plumbing) plus an
end-to-end check that two DIFFERENT viewer IPs behind the same proxy get INDEPENDENT budgets.
"""
import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.public_routes import DEFAULT_TRUSTED_HOPS, PublicDeps, _trusted_client_ip
from api.views import SavedViews
from signup.leads import MemoryLeadStore


class _Req:
    """Minimal stand-in for the parts of starlette.Request _trusted_client_ip reads."""

    def __init__(self, xff=None, peer="10.0.0.9"):
        self.headers = {} if xff is None else {"x-forwarded-for": xff}

        class _Client:
            host = peer

        self.client = _Client() if peer is not None else None


def test_picks_viewer_ip_two_hops_from_right_by_default():
    # XFF = <spoofable...>, <real viewer (CloudFront)>, <CloudFront edge (ALB)>
    r = _Req(xff="9.9.9.9, 203.0.113.7, 70.132.0.1")
    assert _trusted_client_ip(r) == "203.0.113.7"
    assert DEFAULT_TRUSTED_HOPS == 2


def test_attacker_prefix_is_ignored():
    # The attacker stuffs spoofed IPs on the LEFT — they never become the key.
    a = _Req(xff="1.2.3.4, 203.0.113.7, 70.132.0.1")
    b = _Req(xff="evil-spoof, 203.0.113.7, 70.132.0.1")
    assert _trusted_client_ip(a) == _trusted_client_ip(b) == "203.0.113.7"


def test_hops_one_for_alb_only_topology():
    r = _Req(xff="203.0.113.7, 70.132.0.1")
    assert _trusted_client_ip(r, trusted_hops=1) == "70.132.0.1"


def test_short_chain_falls_back_to_socket_peer_not_attacker_value():
    # Fewer entries than the topology claims (a direct hit / probe) -> the socket peer, never a
    # client-supplied value.
    r = _Req(xff="203.0.113.7", peer="10.0.0.9")     # only 1 entry, default wants 2
    assert _trusted_client_ip(r, trusted_hops=2) == "10.0.0.9"


def test_no_header_falls_back_to_socket_peer():
    assert _trusted_client_ip(_Req(xff=None, peer="10.0.0.9")) == "10.0.0.9"


def test_no_peer_and_no_header_is_stable_nonempty():
    assert _trusted_client_ip(_Req(xff=None, peer=None)) == "0.0.0.0"


def test_junk_hops_falls_back_to_default():
    r = _Req(xff="9.9.9.9, 203.0.113.7, 70.132.0.1")
    assert _trusted_client_ip(r, trusted_hops=0) == "203.0.113.7"     # 0 -> default 2
    assert _trusted_client_ip(r, trusted_hops=-5) == "203.0.113.7"


# --- end-to-end: two viewers behind the same proxy do NOT share a budget ----------------------

class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _app(rate=2):
    store = MemoryLeadStore()
    public = PublicDeps(leads_store=store, rate_per_minute=rate, trusted_hops=2, now=Clock())
    deps = ApiDeps(verifier=object(), greenlight=Greenlight(), saved_views=SavedViews(),
                   conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
                   executor=lambda a: None, public=public)
    return TestClient(create_app(deps)), store


GOOD = {"kind": "book_call", "name": "Ada", "email": "ada@example.com"}


@pytest.mark.integration
def test_distinct_viewers_have_independent_budgets():
    client, store = _app(rate=2)
    edge = "70.132.0.1"
    viewer_a = lambda: {"x-forwarded-for": f"203.0.113.7, {edge}"}    # noqa: E731
    viewer_b = lambda: {"x-forwarded-for": f"198.51.100.4, {edge}"}   # noqa: E731
    # Viewer A burns its budget.
    assert client.post("/public/leads", json=GOOD, headers=viewer_a()).status_code == 201
    assert client.post("/public/leads", json=GOOD, headers=viewer_a()).status_code == 201
    assert client.post("/public/leads", json=GOOD, headers=viewer_a()).status_code == 429
    # Viewer B — same ALB edge — is UNAFFECTED (the bug would have 429'd it).
    assert client.post("/public/leads", json=GOOD, headers=viewer_b()).status_code == 201
    # source_ip stored is the trust-boundary viewer IP, not the ALB edge.
    assert store.rows[0]["source_ip"] == "203.0.113.7"
    assert store.rows[-1]["source_ip"] == "198.51.100.4"
