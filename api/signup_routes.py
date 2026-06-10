"""Public signup + Stripe webhook routes (Build Guide Phase 10, mounted on the control-plane API).

These are PRE-tenant and unauthenticated: the account has no tenant_id yet (it is minted at
provisioning). The Stripe webhook is authenticated by its SIGNATURE, not a JWT — and it is the ONLY
thing that triggers provisioning. The signup logic itself lives in `signup/` and is injected here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from signup.accounts import AccountService
from signup.payment import PaymentError, PaymentService


@dataclass
class SignupDeps:
    accounts: AccountService
    payment: PaymentService            # constructed with on_paid -> provisioner.provision
    stripe_webhook_secret: str
    new_account_id: Callable[[], str]
    email_token_ok: Callable[[str, str], bool]
    sms_code_ok: Callable[[str, str], bool]
    # Where the browser GET click-through (the emailed link) lands AFTER the token is consumed:
    # the SPA base from shared.config Config.signup_verify_url_base. Empty (the safe fallback)
    # = no redirect; the route answers JSON like its POST sibling.
    verify_redirect_url: str = ""


class SignupBody(BaseModel):
    email: str
    phone: str


class VerifyEmailBody(BaseModel):
    token: str


class VerifyPhoneBody(BaseModel):
    code: str


class CheckoutBody(BaseModel):
    plan: str


def mount_signup(app: FastAPI, deps: SignupDeps) -> None:
    @app.post("/signup")
    def signup(body: SignupBody):
        account_id = deps.new_account_id()
        acct = deps.accounts.create(account_id, body.email, body.phone)
        return {"account_id": acct.id, "state": acct.state.value}

    @app.get("/signup/{account_id}")
    def signup_state(account_id: str):
        acct = deps.accounts.store.get(account_id)
        if acct is None:
            raise HTTPException(status_code=404, detail="no such account")
        return {"account_id": acct.id, "state": acct.state.value, "tenant_id": acct.tenant_id}

    @app.post("/signup/{account_id}/verify-email")
    def verify_email(account_id: str, body: VerifyEmailBody):
        if deps.accounts.store.get(account_id) is None:
            raise HTTPException(status_code=404, detail="no such account")
        acct = deps.accounts.verify_email(account_id, deps.email_token_ok(account_id, body.token))
        return {"state": acct.state.value, "email_verified": acct.email_verified}

    @app.get("/signup/{account_id}/verify-email")
    def verify_email_click(account_id: str, token: str = ""):
        """The emailed link (a browser GET). Verification itself is the same constant-time path
        as the POST sibling (`EmailTokenService.verify` MACs the token before anything is decoded
        or branched on); a bad/expired/replayed token just doesn't flip the flag. Then a 303 so
        the browser lands on the SPA with a clean GET — or JSON when no SPA base is configured."""
        if deps.accounts.store.get(account_id) is None:
            raise HTTPException(status_code=404, detail="no such account")
        acct = deps.accounts.verify_email(account_id, deps.email_token_ok(account_id, token))
        if deps.verify_redirect_url:
            sep = "&" if "?" in deps.verify_redirect_url else "?"
            dest = (
                f"{deps.verify_redirect_url}{sep}account_id={quote(account_id, safe='')}"
                f"&email_verified={'1' if acct.email_verified else '0'}"
            )
            return RedirectResponse(dest, status_code=303)
        return {"state": acct.state.value, "email_verified": acct.email_verified}

    @app.post("/signup/{account_id}/verify-phone")
    def verify_phone(account_id: str, body: VerifyPhoneBody):
        if deps.accounts.store.get(account_id) is None:
            raise HTTPException(status_code=404, detail="no such account")
        acct = deps.accounts.verify_phone(account_id, deps.sms_code_ok(account_id, body.code))
        return {"state": acct.state.value, "phone_verified": acct.phone_verified}

    @app.post("/signup/{account_id}/checkout")
    def checkout(account_id: str, body: CheckoutBody, request: Request):
        if deps.accounts.store.get(account_id) is None:
            raise HTTPException(status_code=404, detail="no such account")
        # Idempotency key from the client (a double-click reuses it) or derived deterministically.
        idem = request.headers.get("idempotency-key") or f"{account_id}:{body.plan}"
        try:
            res = deps.payment.start_checkout(account_id, body.plan, idem)
        except PaymentError as e:
            # e.g. not yet verified (verify before pay)
            raise HTTPException(status_code=400, detail=str(e))
        return {"checkout_id": res.checkout_id, "stripe_customer_id": res.stripe_customer_id}

    @app.post("/webhooks/stripe")
    async def stripe_webhook(request: Request):
        payload = await request.body()
        sig = request.headers.get("stripe-signature", "")
        try:
            # The ONLY provisioning trigger. Signature-verified inside handle_webhook.
            return deps.payment.handle_webhook(payload, sig, deps.stripe_webhook_secret)
        except Exception as e:  # noqa: BLE001 — bad signature / malformed
            raise HTTPException(status_code=400, detail=f"webhook rejected: {type(e).__name__}")
