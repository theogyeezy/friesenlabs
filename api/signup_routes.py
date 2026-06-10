"""Public signup + Stripe webhook routes (Build Guide Phase 10, mounted on the control-plane API).

These are PRE-tenant and unauthenticated: the account has no tenant_id yet (it is minted at
provisioning). The Stripe webhook is authenticated by its SIGNATURE, not a JWT — and it is the ONLY
thing that triggers provisioning. The signup logic itself lives in `signup/` and is injected here.

ONE exception: POST /signup/{account_id}/retry-provision (INT/P2) is POST-payment and GATED —
disabled (404) unless prod wires it under SIGNUP_REAL_DEPS, and it demands a VERIFIED Cognito
claim whose custom:tenant_id matches the account (else it stays internal-only; see the route).
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
    # --- POST /signup/{account_id}/retry-provision (TODO INT/P2 closure) — two layered gates:
    # `retry_provision(account_id) -> dict` runs the idempotent in-process retry
    # (signup.provisioning.Provisioner.retry — ACTIVE = skip, non-parked = structured refusal).
    # None (the default) = the route answers 404: api/prod_deps wires it ONLY under the
    # SIGNUP_REAL_DEPS master switch, so an unconfigured deploy is byte-identical to the route
    # not existing.
    retry_provision: Callable[[str], dict] | None = None
    # `claims_tenant(request) -> api.auth.TenantClaims` — THE TRUST RULE gate: verifies the
    # bearer JWT against the Cognito pool JWKS and yields the `custom:tenant_id` claim (raises
    # HTTPException 401 itself on a missing/invalid token). The route requires the verified
    # claim to MATCH the account's provisioner-minted tenant_id. None (the default) = no
    # verifier wired -> the route REFUSES (403) and stays internal-only: there is no admin auth
    # seam in this API, so without claims the only retry surface is the operator's direct
    # Lambda 'retry' invoke (signup/lambda_handler.py — IAM-gated by lambda:InvokeFunction).
    claims_tenant: Callable | None = None


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

    @app.post("/signup/{account_id}/retry-provision")
    def retry_provision(account_id: str, request: Request):
        """Re-provision a parked (provisioning_failed) account (TODO INT/P2 closure).

        NOT a payment path — provisioning still fires only off the signed Stripe webhook; this
        re-runs the idempotent pipeline for an account ALREADY past payment whose build failed.
        Gate order (each fails CLOSED — see the SignupDeps field docs):
          1. enabled at all?         retry_provision wired only under SIGNUP_REAL_DEPS -> 404
          2. claims verifier wired?  no admin-auth seam exists, so without the Cognito JWKS
                                     verifier the route is internal-only -> 403 (operator path
                                     = the direct Lambda 'retry' invoke)
          3. verified claim          deps.claims_tenant raises 401 on a missing/bad token
          4. account exists?         404 (checked AFTER auth — no unauthenticated id oracle)
          5. tenant match            the verified custom:tenant_id must equal the account's
                                     provisioner-minted tenant_id -> else 403. An early-step
                                     failure (no tenant minted yet) can never match — those
                                     parked accounts are operator-retry-only by design.
        """
        if deps.retry_provision is None:
            raise HTTPException(status_code=404, detail="retry-provision not enabled")
        if deps.claims_tenant is None:
            raise HTTPException(
                status_code=403,
                detail="retry-provision is internal-only here (no claims verifier wired); "
                       "use the operator Lambda retry entrypoint",
            )
        claims = deps.claims_tenant(request)   # raises HTTPException(401) on bad/missing token
        acct = deps.accounts.store.get(account_id)
        if acct is None:
            raise HTTPException(status_code=404, detail="no such account")
        if not acct.tenant_id or str(claims.tenant_id) != str(acct.tenant_id):
            raise HTTPException(status_code=403,
                                detail="tenant claim does not match this account")
        return deps.retry_provision(account_id)

    @app.post("/webhooks/stripe")
    async def stripe_webhook(request: Request):
        payload = await request.body()
        sig = request.headers.get("stripe-signature", "")
        try:
            # The ONLY provisioning trigger. Signature-verified inside handle_webhook.
            return deps.payment.handle_webhook(payload, sig, deps.stripe_webhook_secret)
        except Exception as e:  # noqa: BLE001 — bad signature / malformed
            raise HTTPException(status_code=400, detail=f"webhook rejected: {type(e).__name__}")
