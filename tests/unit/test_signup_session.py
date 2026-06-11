"""Unit: shared/signup_session.py — signed, scoped, expiring signup-session tokens.

Proves the security contract that lets a token REPLACE the bare-bearer account_id:
  * forge resistance — a wrong secret never verifies; tampering body or signature -> None;
  * scope binding — a token minted for one scope never satisfies another;
  * expiry — a token past its TTL stops verifying;
  * resolve_account_id rollout contract — wired tokens take precedence, a token-shaped value
    that fails verification is rejected (not silently treated as a raw id), raw ids still pass.
"""
import pytest

from shared.signup_session import (
    BadScope,
    SignupSessionTokens,
    resolve_account_id,
)


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def _svc(secret="s3cr3t-signing-key", ttl=1800, clock=None):
    return SignupSessionTokens(secret, ttl_seconds=ttl, now=clock or Clock())


def test_round_trip_returns_bound_account_id():
    svc = _svc()
    tok = svc.mint("acct-abc", "checkout")
    assert svc.verify(tok, "checkout") == "acct-abc"


def test_empty_secret_refused_at_construction():
    with pytest.raises(ValueError):
        SignupSessionTokens("")


def test_unknown_scope_at_mint_is_loud():
    with pytest.raises(BadScope):
        _svc().mint("acct-abc", "admin")


def test_unknown_scope_at_verify_is_none_not_raise():
    svc = _svc()
    tok = svc.mint("acct-abc", "checkout")
    assert svc.verify(tok, "admin") is None      # never raises on a bad scope argument


def test_wrong_scope_does_not_verify():
    svc = _svc()
    state_tok = svc.mint("acct-abc", "state")
    # A read-only `state` token must NOT unlock `checkout` or `bypass`.
    assert svc.verify(state_tok, "checkout") is None
    assert svc.verify(state_tok, "bypass") is None
    assert svc.verify(state_tok, "state") == "acct-abc"


def test_forged_signature_rejected():
    real = _svc(secret="real-key")
    attacker = _svc(secret="attacker-key")
    forged = attacker.mint("acct-abc", "checkout")
    # Minted under a different secret -> our verify must reject it.
    assert real.verify(forged, "checkout") is None


def test_tampered_body_rejected():
    svc = _svc()
    tok = svc.mint("acct-abc", "checkout")
    body, _, sig = tok.rpartition(".")
    tampered = body[:-1] + ("A" if body[-1] != "A" else "B") + "." + sig
    assert svc.verify(tampered, "checkout") is None


def test_tampered_signature_rejected():
    svc = _svc()
    tok = svc.mint("acct-abc", "checkout")
    body, _, sig = tok.rpartition(".")
    assert svc.verify(f"{body}.{sig[:-1] + '0'}", "checkout") is None


@pytest.mark.parametrize("garbage", ["", "no-dot", ".", "a.b.c.d", "...."])
def test_malformed_tokens_return_none_never_raise(garbage):
    assert _svc().verify(garbage, "checkout") is None


def test_expiry_stops_verifying():
    clock = Clock()
    svc = _svc(ttl=900, clock=clock)
    tok = svc.mint("acct-abc", "checkout")
    assert svc.verify(tok, "checkout") == "acct-abc"
    clock.advance(901)                            # past TTL
    assert svc.verify(tok, "checkout") is None


# --- resolve_account_id (rollout contract) ---------------------------------------------------

def test_resolve_prefers_verified_token():
    svc = _svc()
    tok = svc.mint("acct-xyz", "checkout")
    assert resolve_account_id(tok, tokens=svc, scope="checkout") == "acct-xyz"


def test_resolve_accepts_raw_account_id_when_not_token_shaped():
    svc = _svc()
    # A plain uuid-like id (no ".") is treated as the legacy raw account_id.
    assert resolve_account_id("plain-account-id", tokens=svc, scope="checkout") == "plain-account-id"


def test_resolve_rejects_token_shaped_but_unverifiable():
    svc = _svc()
    # Has a "." so it LOOKS like a token, but it isn't ours -> None (never falls through to raw).
    assert resolve_account_id("bogus.signature", tokens=svc, scope="checkout") is None


def test_resolve_wrong_scope_token_is_rejected():
    svc = _svc()
    state_tok = svc.mint("acct-xyz", "state")
    assert resolve_account_id(state_tok, tokens=svc, scope="checkout") is None


def test_resolve_accepts_any_of_multiple_scopes():
    svc = _svc()
    checkout_tok = svc.mint("acct-xyz", "checkout")
    state_tok = svc.mint("acct-xyz", "state")
    accepted = ("checkout", "state", "bypass")
    assert resolve_account_id(checkout_tok, tokens=svc, scope="state",
                              accepted_scopes=accepted) == "acct-xyz"
    assert resolve_account_id(state_tok, tokens=svc, scope="state",
                              accepted_scopes=accepted) == "acct-xyz"


def test_resolve_passthrough_when_tokens_unwired():
    # Feature OFF (tokens=None): the path segment is always the legacy raw account_id, even if it
    # happens to contain a "." — no verification attempted, no behavior change pre-rollout.
    assert resolve_account_id("a.b", tokens=None, scope="checkout") == "a.b"
    assert resolve_account_id("raw-id", tokens=None, scope="checkout") == "raw-id"
