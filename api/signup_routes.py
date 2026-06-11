"""Public signup + Stripe webhook routes (Build Guide Phase 10, mounted on the control-plane API).

These are PRE-tenant and unauthenticated: the account has no tenant_id yet (it is minted at
provisioning). The Stripe webhook is authenticated by its SIGNATURE, not a JWT — and it is the ONLY
thing that triggers provisioning. The signup logic itself lives in `signup/` and is injected here.

ONE exception: POST /signup/{account_id}/retry-provision (INT/P2) is POST-payment and GATED —
disabled (404) unless prod wires it under SIGNUP_REAL_DEPS, and it demands a VERIFIED Cognito
claim whose custom:tenant_id matches the account (else it stays internal-only; see the route).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from shared.signup_session import SignupSessionTokens, resolve_account_id
from signup.abuse import (
    ACTION_RESEND,
    ACTION_SIGNUP,
    CaptchaRequiredError,
    CaptchaVerifier,
    DisposableEmailBlocklist,
    DisposableEmailError,
    SignupVelocityLimiter,
    VelocityLimitError,
)
from signup.accounts import AccountService, _require_phone_verification
from signup.payment import PaymentError, PaymentService

# The acquisition funnel (signup + verification-resends) sits behind CloudFront -> ALB -> Fargate,
# exactly like /public/leads. The trusted-IP parse is reused from there (one definition, no
# divergent duplicate) so the velocity limiter keys on the trust-boundary viewer IP, never the
# spoofable left of X-Forwarded-For nor the shared ALB socket peer.
from api.public_routes import DEFAULT_TRUSTED_HOPS, _trusted_client_ip

log = logging.getLogger("api.signup")


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
    # TESTING-ONLY internal Stripe bypass (shared/config.py SIGNUP_INTERNAL_BYPASS_DOMAINS):
    # normalized email domains whose VERIFIED signups settle via PaymentService.internal_comp
    # (the SAME idempotent ledger + on_paid path) instead of Stripe checkout. The default —
    # the empty set — means the feature is OFF and the branch is unreachable.
    internal_bypass_domains: frozenset = frozenset()
    # Signed, scoped, expiring SIGNUP-SESSION tokens (shared/signup_session.py). When wired, the
    # pre-tenant `account_id` is no longer carried as a bare bearer secret on state/checkout/bypass
    # or leaked into the emailed verify-redirect URL — the client carries a short-lived HMAC token
    # scoped to exactly one capability instead. ROLLOUT-COMPATIBLE: the state/checkout path params
    # accept EITHER the new token OR a raw account_id until the web client updates (see
    # `resolve_account_id`); the emailed redirect carries a `state`-scoped token only when this is
    # wired, else it falls back to the legacy raw-account_id query (no behavior change when None).
    # None (the default) = the feature is OFF — byte-identical to the pre-token behavior.
    session_tokens: SignupSessionTokens | None = None
    # --- Acquisition-funnel abuse controls (signup/abuse.py) ---------------------------------
    # All three default to their inert posture so an unwired deploy is byte-identical to before:
    #   * disposable: None  -> no disposable-domain check at signup-start
    #   * velocity:   None  -> no per-IP signup/resend velocity cap
    #   * captcha:    None  -> the seam is absent (no token ever required)
    # api/prod_deps.py builds the real ones from env (always-safe defaults — the static blocklist
    # ships in-repo; the velocity limiter is in-process; the captcha seam defaults OPEN).
    disposable: DisposableEmailBlocklist | None = None
    velocity: SignupVelocityLimiter | None = None
    captcha: CaptchaVerifier | None = None
    # Trusted proxy hops in front of the API for the X-Forwarded-For parse (CloudFront -> ALB = 2).
    # Reused from api/public_routes — never key the limiter on the shared ALB socket peer.
    trusted_hops: int = DEFAULT_TRUSTED_HOPS


class SignupBody(BaseModel):
    email: str
    phone: str
    # Optional user-chosen password — carried securely over HTTPS and passed directly to
    # Cognito via admin_set_user_password(Permanent=True). NEVER logged, stored in the DB,
    # or echoed in the response. When absent (older clients / back-compat), provisioning
    # falls back to the existing generated-password + forgot-password onboarding path.
    password: str | None = None


class VerifyEmailBody(BaseModel):
    token: str


class VerifyPhoneBody(BaseModel):
    code: str


class CheckoutBody(BaseModel):
    plan: str


def mount_signup(app: FastAPI, deps: SignupDeps) -> None:
    # --- account_id resolution under the rollout-compatible session-token contract -----------
    # The {account_id} path segment may be a raw account_id (legacy) OR a signed session token.
    # `_resolve` returns the trusted account_id, or raises 404 for a value that LOOKS like a token
    # (has a ".") but fails to verify for any accepted scope — a forged/expired/wrong-scope token
    # must never fall through to a raw-id lookup. `accepted` defaults to the route's own scope.
    def _resolve(raw: str, scope: str, accepted=None) -> str:
        account_id = resolve_account_id(
            raw, tokens=deps.session_tokens, scope=scope, accepted_scopes=accepted
        )
        if account_id is None:
            raise HTTPException(status_code=404, detail="no such account")
        return account_id

    # A state-read token also unlocks the SPA's later checkout call within the window, so the
    # broad pre-auth reads accept any of the issued scopes; checkout/bypass demand `checkout`.
    _ANY_SESSION = ("checkout", "state", "bypass")

    def _viewer_ip(request: Request) -> str:
        # The trust-boundary viewer IP (reused parse) the abuse limiter keys on — same value the
        # leads endpoint uses, so signup + leads share one consistent notion of "the requester".
        return _trusted_client_ip(request, deps.trusted_hops)

    def _velocity(action: str, request: Request) -> None:
        if deps.velocity is None:
            return
        try:
            deps.velocity.check(action, _viewer_ip(request))
        except VelocityLimitError as e:
            raise HTTPException(status_code=429, detail=str(e))

    @app.post("/signup")
    def signup(body: SignupBody, request: Request):
        # --- abuse gates, BEFORE any account/Cognito/email work (so a bad actor can't run up the
        # --- Anthropic-adjacent signup path or spam verification emails) ---
        # 1) per-IP signup velocity (429). Re-POSTing /signup re-sends the verification email
        #    (create is idempotent), so this also bounds email-resend spam from one address.
        _velocity(ACTION_SIGNUP, request)
        # 2) CAPTCHA seam — no-op (OPEN) by default; only enforces once a real verifier is wired.
        if deps.captcha is not None:
            try:
                deps.captcha.verify(request.headers.get("x-captcha-token"), _viewer_ip(request))
            except CaptchaRequiredError as e:
                raise HTTPException(status_code=400, detail=str(e))
        # 3) disposable / throwaway email domains — honest copy (422), never a silent drop.
        if deps.disposable is not None:
            try:
                deps.disposable.check(body.email)
            except DisposableEmailError as e:
                raise HTTPException(status_code=422, detail=str(e))
        account_id = deps.new_account_id()
        acct = deps.accounts.create(account_id, body.email, body.phone)
        # If the client supplied a password, set it as the user's permanent Cognito credential
        # immediately — so first login works with what they typed. The password is consumed here
        # and never stored, logged, or returned. Older clients (no password field) fall back to
        # the existing generated-password + forgot-password onboarding path at provisioning time.
        if body.password:
            deps.accounts.cognito.set_signup_password(acct.cognito_sub, body.password)
        out = {"account_id": acct.id, "state": acct.state.value,
               # Tell the SPA whether to walk the phone-verify step (SIGNUP_REQUIRE_PHONE feature
               # flag). When false, the SPA skips straight from email-verify to plan selection.
               "require_phone": _require_phone_verification()}
        # When session tokens are wired, hand the SPA a `checkout`-scoped session token so it never
        # has to carry the raw account_id as a bearer secret on the follow-up calls. Returned in
        # the JSON BODY (not a URL) — it does not leak via Referer/logs the way the emailed link
        # would. Backward-compatible: account_id is still returned during the rollout window.
        if deps.session_tokens is not None:
            out["session_token"] = deps.session_tokens.mint(acct.id, "checkout")
        return out

    @app.get("/signup/{account_id}")
    def signup_state(account_id: str):
        account_id = _resolve(account_id, "state", accepted=_ANY_SESSION)
        acct = deps.accounts.store.get(account_id)
        if acct is None:
            raise HTTPException(status_code=404, detail="no such account")
        # PRE-AUTH endpoint: do NOT leak tenant_id here. The provisioner-minted tenant_id is the
        # value THE TRUST RULE binds against (retry-provision claim match); exposing it on an
        # unauthenticated, account_id-keyed read hands an attacker the other half of that pair.
        # The SPA learns its tenant only after auth (the verified Cognito custom:tenant_id claim).
        return {"account_id": acct.id, "state": acct.state.value}

    @app.post("/signup/{account_id}/verify-email")
    def verify_email(account_id: str, body: VerifyEmailBody, request: Request):
        # Per-IP velocity on verification attempts (resend/retry surface): bounds token-guessing +
        # resend spam from one address (429). The token itself is still constant-time verified.
        _velocity(ACTION_RESEND, request)
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
            # Carry a SHORT-LIVED, state-SCOPED session token to the SPA — NOT the raw account_id.
            # This redirect Location leaks via the Referer header + access logs; a `state` token
            # (read-only, expiring) is near-useless if leaked, whereas the raw account_id is a
            # bare bearer secret good for checkout + bypass. Falls back to the legacy account_id
            # query only when session tokens are not wired (no behavior change pre-rollout).
            if deps.session_tokens is not None:
                carrier = f"session_token={quote(deps.session_tokens.mint(account_id, 'state'), safe='')}"
            else:
                carrier = f"account_id={quote(account_id, safe='')}"
            dest = (
                f"{deps.verify_redirect_url}{sep}{carrier}"
                f"&email_verified={'1' if acct.email_verified else '0'}"
            )
            return RedirectResponse(dest, status_code=303)
        return {"state": acct.state.value, "email_verified": acct.email_verified}

    @app.post("/signup/{account_id}/verify-phone")
    def verify_phone(account_id: str, body: VerifyPhoneBody, request: Request):
        # Same per-IP velocity guard as verify-email — bounds OTP-guessing/resend spam per IP. The
        # per-ACCOUNT OTP send/attempt budget (signup.tokens.OtpService) is the complementary layer.
        _velocity(ACTION_RESEND, request)
        if deps.accounts.store.get(account_id) is None:
            raise HTTPException(status_code=404, detail="no such account")
        acct = deps.accounts.verify_phone(account_id, deps.sms_code_ok(account_id, body.code))
        return {"state": acct.state.value, "phone_verified": acct.phone_verified}

    @app.post("/signup/{account_id}/checkout")
    def checkout(account_id: str, body: CheckoutBody, request: Request):
        # Checkout + the internal-comp bypass are the high-capability pre-auth actions, so a
        # session token here must carry the `checkout` scope (a `state`-only read token is
        # rejected). Legacy raw account_id still accepted during rollout.
        account_id = _resolve(account_id, "checkout")
        acct = deps.accounts.store.get(account_id)
        if acct is None:
            raise HTTPException(status_code=404, detail="no such account")
        # TESTING-ONLY internal bypass (default OFF — empty set): a VERIFIED signup whose
        # SERVER-STORED email domain is allow-listed settles via the SAME idempotent ledger +
        # on_paid path as the webhook (PaymentService.internal_comp), with no Stripe call.
        # The domain comes from the account row (set + normalized at signup, then verified),
        # never from anything the client sends on this request.
        domain = (acct.email or "").rsplit("@", 1)[-1].lower()
        if deps.internal_bypass_domains and domain in deps.internal_bypass_domains:
            try:
                res = deps.payment.internal_comp(account_id, body.plan)
            except PaymentError as e:
                # Server-side log keeps the real reason; the client gets a FIXED message —
                # str(e) can carry adapter/provider internals (an unauthenticated reconnaissance
                # surface), and other paths here already echo only type(exc).__name__ at most.
                log.warning("internal_comp refused for account %s: %s: %s",
                            account_id, type(e).__name__, e)
                raise HTTPException(status_code=400, detail="payment could not be completed")
            return {"checkout_url": None, "bypass": "internal_comp", **res}
        # Idempotency key from the client (a double-click reuses it) or derived deterministically.
        idem = request.headers.get("idempotency-key") or f"{account_id}:{body.plan}"
        try:
            res = deps.payment.start_checkout(account_id, body.plan, idem)
        except PaymentError as e:
            # e.g. not yet verified (verify before pay). The real reason goes to the server log
            # ONLY: str(e) wraps Stripe adapter errors verbatim, and echoing provider internals
            # on an unauthenticated route is a reconnaissance gift (the repo pattern elsewhere
            # in this file returns at most type(exc).__name__).
            log.warning("start_checkout failed for account %s: %s: %s",
                        account_id, type(e).__name__, e)
            raise HTTPException(status_code=400, detail="payment could not be started")
        # checkout_url is the Stripe-hosted page the SPA must send the browser to. Returning it
        # (instead of discarding it) is what makes the revenue path real — the client no longer
        # fakes payment success; the signed webhook remains the only provisioning trigger.
        return {
            "checkout_id": res.checkout_id,
            "stripe_customer_id": res.stripe_customer_id,
            "checkout_url": res.checkout_url,
        }

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
