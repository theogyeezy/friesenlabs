"""Unit: member-upsert-on-auth — the Sell roster refresh threaded into the verified-JWT dependency.

The shared auth dependency (api.auth.make_current_tenant) gains an OPTIONAL member store. With it
wired, a successful auth upserts the caller's `members` row from the VERIFIED claims (sub + name);
THE TRUST RULE holds (identity from the claim, never a header/body). It is ADDITIVE + GUARDED:

  * no member store wired  -> a pure no-op (the legacy behaviour, unchanged)
  * the unauth path        -> 401 with the store NEVER touched
  * a member-store failure -> swallowed; auth still succeeds

These are pure-Python (a fake verifier + a fake request + the InMemoryMemberStore) — no DB, no app.
"""
import pytest

from api.auth import make_current_tenant
from api.gamify_stores import InMemoryMemberStore
from fastapi import HTTPException


class _FakeRequest:
    def __init__(self, authorization: str | None):
        self.headers = {"authorization": authorization} if authorization is not None else {}


class _FakeVerifier:
    def __init__(self, claims):
        self._claims = claims

    def verify(self, token):
        return dict(self._claims)


class _BoomStore:
    def upsert(self, *a, **k):
        raise RuntimeError("member store is down")


@pytest.mark.unit
def test_authed_request_upserts_member_from_claims():
    members = InMemoryMemberStore()
    verifier = _FakeVerifier({
        "sub": "u-alice", "custom:tenant_id": "T1", "email": "alice@x.com", "name": "Alice",
    })
    dep = make_current_tenant(verifier, member_store=members)

    claims = dep(_FakeRequest("Bearer tok"))

    # The verified claims flow through unchanged...
    assert claims.tenant_id == "T1"
    assert claims.sub == "u-alice"
    # ...and the roster row was upserted from the SAME claims (name claim -> display_name).
    roster = members.list("T1")
    assert len(roster) == 1
    assert roster[0]["user_id"] == "u-alice"
    assert roster[0]["display_name"] == "Alice"
    # Scoped to the verified tenant only.
    assert members.list("OTHER") == []


@pytest.mark.unit
def test_display_name_falls_back_to_email_when_no_name_claim():
    members = InMemoryMemberStore()
    verifier = _FakeVerifier({"sub": "u-bob", "custom:tenant_id": "T1", "email": "bob@x.com"})
    dep = make_current_tenant(verifier, member_store=members)

    dep(_FakeRequest("Bearer tok"))

    assert members.list("T1")[0]["display_name"] == "bob@x.com"


@pytest.mark.unit
def test_no_member_store_is_a_noop():
    # The legacy path: no store wired -> nothing upserts, claims resolve exactly as before.
    verifier = _FakeVerifier({"sub": "u-alice", "custom:tenant_id": "T1"})
    dep = make_current_tenant(verifier)  # member_store defaults to None

    claims = dep(_FakeRequest("Bearer tok"))
    assert claims.tenant_id == "T1" and claims.sub == "u-alice"


@pytest.mark.unit
def test_unauth_request_never_touches_the_store():
    members = InMemoryMemberStore()
    verifier = _FakeVerifier({"sub": "u", "custom:tenant_id": "T1"})
    dep = make_current_tenant(verifier, member_store=members)

    # No bearer -> 401 before any verification/upsert.
    with pytest.raises(HTTPException) as ei:
        dep(_FakeRequest(None))
    assert ei.value.status_code == 401
    assert members.list("T1") == []  # the store was never touched


@pytest.mark.unit
def test_member_store_failure_never_breaks_auth():
    # A roster write that blows up must be swallowed — authentication still succeeds.
    verifier = _FakeVerifier({"sub": "u-alice", "custom:tenant_id": "T1", "email": "a@x.com"})
    dep = make_current_tenant(verifier, member_store=_BoomStore())

    claims = dep(_FakeRequest("Bearer tok"))
    assert claims.tenant_id == "T1" and claims.sub == "u-alice"
