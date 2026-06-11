"""Account lifecycle (Build Guide Phase 10, Steps 52-53).

A pending account moves: created -> email_verified -> phone_verified -> paid -> provisioning ->
active (or provisioning_failed). VERIFY BEFORE PAY: payment is only allowed once email + phone are
verified (the cheap guard against typo'd emails / charging the wrong person). tenant_id does NOT exist
yet — it is minted at provisioning (Step 55).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum


def _require_phone_verification() -> bool:
    """Feature flag SIGNUP_REQUIRE_PHONE (default true). Set "false" to launch on email-only
    verification while SMS account-level approval is pending. Mirrors shared.config.Config —
    read directly here to keep the readiness property cheap + side-effect-free."""
    return os.environ.get("SIGNUP_REQUIRE_PHONE", "true").strip().lower() != "false"

# Basic RFC-ish email shape (not a full RFC 5322 parser — a cheap server-side guard against
# typos / obviously-malformed input). One '@', a dotted domain, no whitespace.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# A small denylist of obvious throwaway/disposable email domains. Not exhaustive — a cheap
# first-pass filter; deeper abuse detection lives elsewhere.
_DISPOSABLE_DOMAINS = frozenset({
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "throwawaymail.com", "yopmail.com", "trashmail.com", "getnada.com",
    "sharklasers.com", "discard.email",
})


def normalize_email(email: str) -> str:
    """Validate + normalize an email to lowercase. Raises ValueError on bad/disposable input."""
    if not isinstance(email, str):
        raise ValueError("email must be a string")
    normalized = email.strip().lower()
    if not _EMAIL_RE.match(normalized):
        raise ValueError(f"invalid email address: {email!r}")
    domain = normalized.rsplit("@", 1)[1]
    if domain in _DISPOSABLE_DOMAINS:
        raise ValueError(f"disposable email domains are not allowed: {domain}")
    return normalized


def normalize_phone(phone: str) -> str:
    """Normalize to E.164-ish: an optional leading '+' followed by digits only.

    Strips spaces, dashes, parens, and dots. Requires a non-empty digit run and (after a
    cheap length sanity check) returns '+<digits>'. Raises ValueError on anything else.
    """
    if not isinstance(phone, str):
        raise ValueError("phone must be a string")
    raw = phone.strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        raise ValueError(f"invalid phone number: {phone!r}")
    if len(digits) > 15:  # E.164 max is 15 digits
        raise ValueError(f"phone number too long: {phone!r}")
    # If the caller wrote stray non-digit/non-formatting junk (e.g. letters), reject it.
    if re.sub(r"[\s\-().+]", "", raw) != digits:
        raise ValueError(f"invalid phone number: {phone!r}")
    return "+" + digits


class State(str, Enum):
    CREATED = "created"
    EMAIL_VERIFIED = "email_verified"
    PHONE_VERIFIED = "phone_verified"
    PAID = "paid"
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    PROVISIONING_FAILED = "provisioning_failed"


@dataclass
class Account:
    id: str
    email: str
    phone: str
    cognito_sub: str                      # Cognito user (unconfirmed, NO tenant_id yet)
    state: State = State.CREATED
    email_verified: bool = False
    phone_verified: bool = False
    stripe_customer_id: str | None = None
    tenant_id: str | None = None          # minted only at provisioning
    meta: dict = field(default_factory=dict)

    @property
    def fully_verified(self) -> bool:
        # Phone gated behind SIGNUP_REQUIRE_PHONE (default on). When off, email alone is "fully
        # verified" — the email-only launch path while SMS approval is pending.
        if not _require_phone_verification():
            return self.email_verified
        return self.email_verified and self.phone_verified

    @property
    def may_pay(self) -> bool:
        # VERIFY BEFORE PAY.
        return self.fully_verified


class AccountService:
    def __init__(self, store, cognito, email_sender, sms):
        self.store = store            # injected: get/insert/update
        self.cognito = cognito        # injected: create_unconfirmed_user / set_tenant_id / confirm
        self.email = email_sender     # injected: Resend
        self.sms = sms                # injected: SNS/Twilio

    def create(self, account_id: str, email: str, phone: str) -> Account:
        existing = self.store.get(account_id)
        if existing:                  # idempotent: re-submitting signup returns the same account
            return existing
        # Server-side validation + normalization (never trust the browser): a clear ValueError
        # is raised on bad email / disposable domain / malformed phone.
        email = normalize_email(email)
        phone = normalize_phone(phone)
        # Enforce uniqueness by (normalized) email: a second signup with the same email returns
        # the already-existing account rather than minting a duplicate Cognito user / row.
        dup = self._get_by_email(email)
        if dup is not None:
            return dup
        sub = self.cognito.create_unconfirmed_user(email=email)  # no tenant_id at this point
        acct = Account(id=account_id, email=email, phone=phone, cognito_sub=sub)
        self.store.insert(acct)
        self.email.send_verification(email, account_id)          # signed single-use 15-min link
        return acct

    def _get_by_email(self, email: str) -> Account | None:
        """Find an existing account by normalized email.

        Prefers a store-provided `get_by_email` (production / indexed lookup); otherwise falls
        back to scanning the store's rows (the in-memory test fake). Returns None if neither is
        available so uniqueness degrades gracefully rather than crashing.
        """
        getter = getattr(self.store, "get_by_email", None)
        if callable(getter):
            return getter(email)
        rows = getattr(self.store, "rows", None)
        if isinstance(rows, dict):
            for acct in rows.values():
                if getattr(acct, "email", None) == email:
                    return acct
        return None

    def verify_email(self, account_id: str, token_ok: bool) -> Account:
        acct = self.store.get(account_id)
        if token_ok:
            acct.email_verified = True
            if acct.fully_verified:
                # Ready to pay — both legs done, OR phone isn't required (SIGNUP_REQUIRE_PHONE off).
                # Advance to the ready-to-pay state (PHONE_VERIFIED) instead of getting stuck; never
                # downgrade an account already past verification.
                if acct.state in (State.CREATED, State.EMAIL_VERIFIED):
                    acct.state = State.PHONE_VERIFIED
            elif acct.state is State.CREATED:
                acct.state = State.EMAIL_VERIFIED
            self.store.update(acct)
        return acct

    def verify_phone(self, account_id: str, code_ok: bool) -> Account:
        acct = self.store.get(account_id)
        if code_ok:
            acct.phone_verified = True
            if acct.email_verified:
                acct.state = State.PHONE_VERIFIED
            self.store.update(acct)
        return acct
