"""Signed, scoped, expiring signup-session tokens (pre-tenant acquisition flow).

WHY: the pre-tenant `account_id` is a bare bearer secret — knowing it is enough to read an
account's state, start its checkout, or settle its internal-comp bypass. It is also placed in
the emailed verification redirect URL, where it leaks via the Referer header and access logs.
This helper mints a short-lived HMAC-SHA256 token that carries the account_id PLUS a `scope`
(``state`` / ``checkout`` / ``bypass``) and an expiry, so a leaked token is bounded in both
time and capability and never exposes the raw account_id.

Mirrors the proven shape of ``signup.tokens.EmailTokenService``: signature is checked
CONSTANT-TIME over the exact encoded body BEFORE anything attacker-controlled is decoded or
branched on, so a tampered token costs the same as a valid one (no timing oracle on the secret).
Unlike the email token these are NOT single-use (a checkout may be retried within the window);
single-use is enforced upstream where it matters (the email click-through verify path).

The signing secret is INJECTED (callers resolve ``shared.config.Config.signup_token_secret_value``
and pass the bytes in). It is never read from env, header, or body here, and never logged.

Import-safe: stdlib only — no network, no driver, nothing at import time.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Iterable

# Constant-time comparison, module-level so tests can confirm EVERY comparison routes through it.
_consteq = hmac.compare_digest

DEFAULT_SESSION_TTL_S = 1800          # 30 minutes — long enough to verify + check out, short
                                      # enough that a leaked token (Referer/log) is near-useless.

# The closed set of capabilities a token may carry. A token minted for one scope never satisfies
# a check for another (a `state` read token cannot start a `checkout`).
SCOPES = frozenset({"state", "checkout", "bypass"})

_SEP = "|"      # body field separator (account_id never contains it — it is a uuid4)


class BadScope(ValueError):
    """Raised at MINT time for an unknown scope — a programming error, fail loud."""


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class SignupSessionTokens:
    """Mint + verify scoped, expiring signup-session tokens.

    Token wire form: ``<base64url(account_id|scope|expiry)>.<hex hmac>``.

      mint(account_id, scope) -> str
      verify(token, scope) -> account_id (str) or None   # None on ANY failure, never raises
    """

    def __init__(self, secret: str | bytes, *, ttl_seconds: int = DEFAULT_SESSION_TTL_S,
                 now=time.time):
        if not secret:
            raise ValueError("signup session secret must be non-empty (inject it; never default)")
        self._secret = secret.encode() if isinstance(secret, str) else bytes(secret)
        self._ttl = int(ttl_seconds)
        self._now = now

    def _sign(self, body_b64: str) -> str:
        return hmac.new(self._secret, body_b64.encode(), hashlib.sha256).hexdigest()

    def mint(self, account_id: str, scope: str) -> str:
        if scope not in SCOPES:
            raise BadScope(f"unknown signup-session scope: {scope!r}")
        expires_at = int(self._now()) + self._ttl
        body = f"{account_id}{_SEP}{scope}{_SEP}{expires_at}".encode()
        body_b64 = _b64u(body)
        return f"{body_b64}.{self._sign(body_b64)}"

    def verify(self, token: str, scope: str) -> str | None:
        """Return the bound account_id iff `token` is OUR signature, carries EXACTLY `scope`, and
        is unexpired. Returns None on any malformed / tampered / wrong-scope / expired input —
        never raises (an attacker-shaped token must not become a 500)."""
        if scope not in SCOPES:
            return None
        if not isinstance(token, str) or "." not in token:
            return None
        body_b64, _, sig = token.rpartition(".")
        # 1. Signature first, constant-time, over the exact encoded body — nothing attacker-
        #    controlled is decoded or branched on until the MAC proves we minted it.
        if not _consteq(self._sign(body_b64), sig):
            return None
        try:
            account_id, tok_scope, expiry_s = _b64u_decode(body_b64).decode().split(_SEP, 2)
            expires_at = int(expiry_s)
        except (ValueError, UnicodeDecodeError):
            return None
        # 2. Scope must match EXACTLY (constant-time — a `state` token never satisfies `checkout`).
        if not _consteq(tok_scope.encode(), scope.encode()):
            return None
        # 3. TTL.
        if int(self._now()) > expires_at:
            return None
        return account_id


def resolve_account_id(
    raw: str,
    *,
    tokens: SignupSessionTokens | None,
    scope: str,
    accepted_scopes: Iterable[str] | None = None,
) -> str | None:
    """Resolve the path segment to an account_id under a ROLLOUT-COMPATIBLE contract.

    During the rollout window the web client may still send a raw `account_id` OR the new signed
    session token. Resolution order:

      1. If `tokens` is wired AND `raw` verifies as a session token for `scope` (or any of
         `accepted_scopes`), return the token-bound account_id (the trusted path).
      2. Otherwise return `raw` verbatim — the legacy raw-account_id path. The account itself is
         still looked up + state-checked downstream, so this is no weaker than today's behavior;
         the token path is strictly an ADDED defense that supersedes it once the web updates.

    A value that *looks* like a token (contains a ".") but FAILS verification returns None, so a
    forged/expired/wrong-scope token is rejected rather than silently treated as a raw id.
    """
    if tokens is not None and isinstance(raw, str) and "." in raw:
        for sc in (accepted_scopes or [scope]):
            acct = tokens.verify(raw, sc)
            if acct is not None:
                return acct
        return None   # looked like a token but did not verify for any accepted scope -> reject
    return raw
