"""Account lifecycle (Build Guide Phase 10, Steps 52-53).

A pending account moves: created -> email_verified -> phone_verified -> paid -> provisioning ->
active (or provisioning_failed). VERIFY BEFORE PAY: payment is only allowed once email + phone are
verified (the cheap guard against typo'd emails / charging the wrong person). tenant_id does NOT exist
yet — it is minted at provisioning (Step 55).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


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
        sub = self.cognito.create_unconfirmed_user(email=email)  # no tenant_id at this point
        acct = Account(id=account_id, email=email, phone=phone, cognito_sub=sub)
        self.store.insert(acct)
        self.email.send_verification(email, account_id)          # signed single-use 15-min link
        return acct

    def verify_email(self, account_id: str, token_ok: bool) -> Account:
        acct = self.store.get(account_id)
        if token_ok:
            acct.email_verified = True
            if acct.state is State.CREATED:
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
