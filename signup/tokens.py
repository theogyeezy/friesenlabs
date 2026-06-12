"""Signed single-use verification credentials for the pre-tenant signup flow (Phase 10).

Implements the two `prod_deps` verifier seams (`email_token_ok` / `sms_code_ok` — both
`(account_id, presented) -> bool`):

  * Email — `EmailTokenService`: HMAC-SHA256 signed, 15-minute, SINGLE-USE tokens. The token is
    `base64url(account_id|expiry|nonce) . hexsig`; verify recomputes the signature over the exact
    encoded body and compares CONSTANT-TIME before anything is decoded or branched on, so a
    tampered token costs the same as a valid one (no timing oracle on the secret).
  * SMS — `OtpService`: random 6-digit OTP (CSPRNG) with TTL, single-use on success, a verify
    ATTEMPT counter (lockout after N wrong codes — re-issue required), and an issue RATE-LIMIT
    counter (max N sends per rolling window). Only an HMAC of the code is ever persisted.

The signing secret is INJECTED (the caller resolves shared.config.Config.signup_token_secret —
a Secrets Manager reference name — and passes the value in). It is never read from env, header,
or body here, and never logged.

Stores: single-use/rate-limit state lives behind tiny injectable protocols. The in-memory
implementations here are per-process (fine for tests/dev); production injects the Aurora-backed
`signup.store_pg.PgOtpStore` (OTP in accounts.meta jsonb, atomic merge) AND
`signup.store_pg.PgUsedTokenStore` (used email-token nonces, same meta-jsonb approach) so state
survives restarts and is shared across Fargate tasks — without the shared used-nonce store, a
consumed email token would REPLAY on whichever task never saw it, for its whole 15-min TTL.

Import-safe: stdlib only — no network, no driver, nothing at import time.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets as _secrets
import time
from typing import Protocol

# Constant-time comparison, module-level so tests can spy that EVERY credential comparison goes
# through it (never a bare `==` on attacker-controlled bytes).
_consteq = hmac.compare_digest

DEFAULT_EMAIL_TOKEN_TTL_S = 900       # 15 minutes (Build Guide Step 53)
DEFAULT_OTP_TTL_S = 600               # 10 minutes
DEFAULT_OTP_MAX_ATTEMPTS = 5          # wrong codes before lockout (re-issue required)
DEFAULT_OTP_MAX_SENDS = 5             # issues per rolling window
DEFAULT_OTP_SEND_WINDOW_S = 3600      # the rolling window
OTP_DIGITS = 6


class OtpRateLimitError(Exception):
    """Raised when an account asks for more OTPs than the per-window send budget allows."""


# ---------------------------------------------------------------------------
# Email tokens
# ---------------------------------------------------------------------------

class UsedTokenStore(Protocol):
    """Single-use bookkeeping for email-token nonces.

    `account_id` rides along on both calls so a SHARED implementation can keep the used-set on
    the account row itself (`signup.store_pg.PgUsedTokenStore` over accounts.meta jsonb — no new
    table) instead of needing a global nonce index. Implementations: `InMemoryUsedTokenStore`
    (here, per-process) and `PgUsedTokenStore` (Aurora, shared across Fargate tasks).
    """

    def is_used(self, account_id: str, nonce: str) -> bool: ...
    def mark_used(self, account_id: str, nonce: str, expires_at: int) -> None: ...


class InMemoryUsedTokenStore:
    """Per-process used-nonce set with lazy expiry pruning (prod injects a shared store).

    Nonces are 16-byte CSPRNG values (globally unique), so the in-memory map stays nonce-keyed;
    `account_id` is accepted for protocol parity with the account-row-scoped Pg store.
    """

    def __init__(self, now=time.time):
        self._now = now
        self._used: dict[str, int] = {}   # nonce -> expires_at (prune once stale)

    def is_used(self, account_id: str, nonce: str) -> bool:
        self._prune()
        return nonce in self._used

    def mark_used(self, account_id: str, nonce: str, expires_at: int) -> None:
        self._prune()
        self._used[nonce] = int(expires_at)

    def _prune(self) -> None:
        now = int(self._now())
        # A nonce past its expiry can never verify again (TTL check fails first) — safe to drop.
        for n in [n for n, exp in self._used.items() if exp < now]:
            del self._used[n]


class EmailTokenService:
    """HMAC-SHA256 signed, 15-minute, single-use email verification tokens.

    issue()  -> "<base64url(account_id|expiry|nonce)>.<hex hmac>"
    verify() -> constant-time signature check FIRST, then account match, TTL, single-use.
    """

    def __init__(self, secret: str | bytes, *, ttl_seconds: int = DEFAULT_EMAIL_TOKEN_TTL_S,
                 used_store: UsedTokenStore | None = None, now=time.time):
        if not secret:
            raise ValueError("signup token secret must be non-empty (inject it; never default)")
        self._secret = secret.encode() if isinstance(secret, str) else bytes(secret)
        self._ttl = int(ttl_seconds)
        self._used = used_store if used_store is not None else InMemoryUsedTokenStore(now=now)
        self._now = now

    def _sign(self, body_b64: str) -> str:
        return hmac.new(self._secret, body_b64.encode(), hashlib.sha256).hexdigest()

    def issue(self, account_id: str) -> str:
        expires_at = int(self._now()) + self._ttl
        nonce = _secrets.token_urlsafe(16)
        body = f"{account_id}|{expires_at}|{nonce}".encode()
        body_b64 = base64.urlsafe_b64encode(body).rstrip(b"=").decode()
        return f"{body_b64}.{self._sign(body_b64)}"

    def verify(self, account_id: str, token: str) -> bool:
        """True iff `token` is OUR signature, for THIS account, unexpired, and never used before.

        Never raises on malformed input — an attacker-shaped token is just False.
        """
        if not isinstance(token, str) or "." not in token:
            return False
        body_b64, _, sig = token.rpartition(".")
        # 1. Signature first, constant-time, over the exact encoded body — nothing attacker-
        #    controlled is decoded or branched on until the MAC proves we minted it.
        if not _consteq(self._sign(body_b64), sig):
            return False
        try:
            pad = "=" * (-len(body_b64) % 4)
            decoded = base64.urlsafe_b64decode(body_b64 + pad).decode()
            token_account, expiry_s, nonce = decoded.rsplit("|", 2)
            expires_at = int(expiry_s)
        except (ValueError, UnicodeDecodeError):
            return False
        # 2. Bound to the account being verified (a signed token for B never verifies A).
        if not _consteq(token_account.encode(), str(account_id).encode()):
            return False
        # 3. TTL.
        if int(self._now()) > expires_at:
            return False
        # 4. Single-use (replay -> False). Account-scoped: the shared Pg store keeps the used
        #    set on THIS account's row (the token's account binding was proven in step 2).
        if self._used.is_used(str(account_id), nonce):
            return False
        self._used.mark_used(str(account_id), nonce, expires_at)
        return True


# ---------------------------------------------------------------------------
# SMS OTP
# ---------------------------------------------------------------------------

class OtpStore(Protocol):
    """Persistence for one in-flight OTP record per account (a json-friendly dict).

    Implementations: `InMemoryOtpStore` (here) and `signup.store_pg.PgOtpStore`
    (accounts.meta jsonb, atomic merge).
    """

    def get_otp(self, account_id: str) -> dict | None: ...
    def put_otp(self, account_id: str, record: dict) -> None: ...
    def clear_otp(self, account_id: str) -> None: ...


class InMemoryOtpStore:
    """Per-process OTP record store (prod injects PgOtpStore)."""

    def __init__(self):
        self._rows: dict[str, dict] = {}

    def get_otp(self, account_id: str) -> dict | None:
        rec = self._rows.get(str(account_id))
        return dict(rec) if rec else None

    def put_otp(self, account_id: str, record: dict) -> None:
        self._rows[str(account_id)] = dict(record)

    def clear_otp(self, account_id: str) -> None:
        self._rows.pop(str(account_id), None)


class OtpService:
    """Random 6-digit SMS OTP: TTL'd, single-use, attempt-limited, issue-rate-limited.

    Only `hmac(secret, account_id:code)` is persisted — a leaked store row never reveals a live
    code. `verify` compares constant-time and burns the record on success (single-use).
    """

    def __init__(self, secret: str | bytes, store: OtpStore | None = None, *,
                 ttl_seconds: int = DEFAULT_OTP_TTL_S,
                 max_attempts: int = DEFAULT_OTP_MAX_ATTEMPTS,
                 max_sends: int = DEFAULT_OTP_MAX_SENDS,
                 send_window_seconds: int = DEFAULT_OTP_SEND_WINDOW_S,
                 now=time.time):
        if not secret:
            raise ValueError("signup token secret must be non-empty (inject it; never default)")
        self._secret = secret.encode() if isinstance(secret, str) else bytes(secret)
        self._store = store if store is not None else InMemoryOtpStore()
        self._ttl = int(ttl_seconds)
        self._max_attempts = int(max_attempts)
        self._max_sends = int(max_sends)
        self._window = int(send_window_seconds)
        self._now = now

    def _hash(self, account_id: str, code: str) -> str:
        msg = f"{account_id}:{code}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def issue(self, account_id: str) -> str:
        """Mint + persist a fresh OTP; returns the code for the SMS sender to deliver.

        Raises OtpRateLimitError once the account exceeds max_sends within the rolling window
        (the rate-limit COUNTER lives in the persisted record, so it survives across tasks when
        backed by PgOtpStore).
        """
        now = int(self._now())
        prior = self._store.get_otp(account_id) or {}
        window_start = int(prior.get("window_start", 0) or 0)
        sends = int(prior.get("sends", 0) or 0)
        if now >= window_start + self._window:
            window_start, sends = now, 0           # window rolled over — reset the budget
        if sends >= self._max_sends:
            raise OtpRateLimitError(
                f"OTP send budget exhausted ({self._max_sends}/{self._window}s); try later"
            )
        code = f"{_secrets.randbelow(10 ** OTP_DIGITS):0{OTP_DIGITS}d}"
        self._store.put_otp(account_id, {
            "code_hmac": self._hash(account_id, code),
            "expires_at": now + self._ttl,
            "attempts": 0,
            "sends": sends + 1,
            "window_start": window_start,
        })
        return code

    def verify(self, account_id: str, code: str) -> bool:
        """True iff `code` matches the live OTP: constant-time, TTL'd, single-use, attempt-capped."""
        rec = self._store.get_otp(account_id)
        # Compare even when there's no record (against an impossible value) so "no OTP issued"
        # and "wrong code" cost the same — then branch.
        expected = (rec or {}).get("code_hmac") or self._hash(account_id, "\x00never")
        good = _consteq(str(expected), self._hash(account_id, str(code)))
        if rec is None:
            return False
        if int(rec.get("attempts", 0)) >= self._max_attempts:
            return False                            # locked out — a re-issue mints a fresh record
        if int(self._now()) > int(rec.get("expires_at", 0)):
            return False
        if not good:
            # Burn an attempt (preserve the send/rate-limit counters in the same record).
            self._store.put_otp(account_id, {**rec, "attempts": int(rec.get("attempts", 0)) + 1})
            return False
        self._store.clear_otp(account_id)           # single-use: success consumes the OTP
        return True
