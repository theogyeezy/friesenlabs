"""Production dependency wiring for the ASGI app (signup / payment / provisioning plane).

Selects the REAL adapters behind env guards (shared/config.py names; values land via the task
definition — infra/REQUESTS.md REQ-003). EVERY guard falls back to the original stub, so an
unconfigured deploy boots byte-identically to before this wiring existed: /healthz 200, routes
mounted, account creation in-memory, verification off, checkout 400s "needs configuration".

MASTER SWITCH — SIGNUP_REAL_DEPS (deploy invariance; adversarial finding, HIGH): the live API
task ALREADY injects COGNITO_USER_POOL_ID (JWKS) and DB_HOST/DB_NAME/DB_USER/DB_PASS (the
request-path stores) for OTHER features, so the per-adapter guards below, alone, would flip real
Cognito admin calls + live-Aurora signup state on a mere image deploy. NO real adapter is
selected unless `Config.signup_real_deps` is on (env exactly 'true'/'1' — a deliberate Lane Nick
act, REQ-003); without it the build is all-stub and byte-identical no matter what other env vars
happen to be present. The individual guards below sit UNDERNEATH the master switch.

  guard (env, under SIGNUP_REAL_DEPS)  real adapter (else the stub)
  -----------------------------------  ------------------------------------------------------------
  STRIPE_API_KEY              signup.stripe_adapter.StripeAdapter        (else _StubStripe)
  COGNITO_USER_POOL_ID        signup.cognito_admin.CognitoAdminClient    (else _StubCognito)
  RESEND_API_KEY              signup.resend_sender.ResendEmailSender     (else _Noop)
  (always)                    signup.sms_sender.SnsSmsOtpSender — self-gating: it never builds a
                              boto3 client (logs + drops) until ALLOW_REAL_SENDS=true
  ANTHROPIC_ADMIN_KEY         signup.anthropic_admin.AnthropicAdminClient (else _Noop)
  UPLIFT_DB_URL / DB_*        signup.store_pg.{PgAccountStore, PgStripeEventLedger, PgOtpStore}
                              + signup.tenant_defaults.PgTenantDefaults (the step-5 seeder)
                              (else the per-task in-memory _AccountStore / no ledger / in-proc
                              OTP / no tenant_settings seed)
  SIGNUP_TOKEN_SECRET_VALUE   signup.tokens.{EmailTokenService, OtpService} wired into
                              email_token_ok / sms_code_ok + issued at create
                              (else verification stays hardcoded OFF — may_pay never flips)
  POSTHOG_PROJECT_KEY_VALUE   signup.posthog_client.PostHogClient inside signup.funnel.Funnel
                              — server-side payment_succeeded / instance_provisioned /
                              provisioning_failed, grouped by tenant (else funnel=None, no-op)
  COGNITO_USER_POOL_ID        api.auth claims gate for POST /signup/{id}/retry-provision
  + COGNITO_CLIENT_ID         (else claims_tenant=None — the route refuses, internal-only)

DRAFT-GATE (CLAUDE.md hard constraint #2) still stands: the Resend/SNS senders refuse real
delivery unless ALLOW_REAL_SENDS=true regardless of keys, and no live cloud/Anthropic resource is
created unless LANE NICK deliberately injects the corresponding credential. The provisioning
pipeline's Secrets-Manager / agent-plane seams remain _Noop (follow-up TODOs; cube is a
DOCUMENTED no-op by design — see Provisioner._step_tenant_context) — live end-to-end
provisioning stays BLOCKED: Lane Nick until those land and the # VERIFY'd Anthropic Admin
endpoints are confirmed.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid

from api.auth import CognitoJwtVerifier, make_current_tenant
from api.signup_routes import SignupDeps
from shared.config import dsn_from_env, load, stripe_price_ids
from signup.accounts import AccountService
from signup.anthropic_admin import AnthropicAdminClient
from signup.cognito_admin import CognitoAdminClient
from signup.funnel import Funnel
from signup.payment import PaymentService
from signup.posthog_client import PostHogClient
from signup.provisioning import Provisioner
from signup.resend_sender import ResendEmailSender
from signup.sms_sender import SnsSmsOtpSender
from signup.store_pg import PgAccountStore, PgOtpStore, PgStripeEventLedger
from signup.stripe_adapter import StripeAdapter
from signup.tenant_defaults import PgTenantDefaults
from signup.tokens import EmailTokenService, OtpRateLimitError, OtpService

log = logging.getLogger(__name__)


class _AccountStore:
    """In-memory account store (the no-DSN fallback; prod swaps in PgAccountStore)."""

    def __init__(self):
        self.rows: dict[str, object] = {}

    def get(self, account_id):
        return self.rows.get(account_id)

    def get_by_email(self, email):
        return next((a for a in self.rows.values() if getattr(a, "email", None) == email), None)

    def insert(self, acct):
        self.rows[acct.id] = acct

    def update(self, acct):
        self.rows[acct.id] = acct


class _StubCognito:
    def create_unconfirmed_user(self, email):
        return f"stub-sub-{uuid.uuid4()}"  # real Cognito needs COGNITO_USER_POOL_ID

    def set_tenant_id(self, sub, tenant_id):
        pass  # real Cognito admin update needs COGNITO_USER_POOL_ID

    def confirm(self, sub):
        pass


class _Noop:
    """Email / SMS / agent-plane / SM / cube stub — the unconfigured fallback."""

    def ensure(self, **_kw):
        # Agent-plane stub: stable stub ids so provisioning can upsert a tenant_workspaces row
        # offline (the conversation factory then resolves them; FakeRuntime accepts any ids —
        # and api/asgi.py REFUSES to hand 'stub-' ids to a real runtime).
        # The real agent plane returns the LIVE workspace/environment/coordinator ids.
        return {"workspace_id": "stub-ws", "environment_id": "stub-env",
                "coordinator_id": "stub-coord"}

    def __getattr__(self, _name):
        def _f(*a, **k):
            return None
        return _f


class _StubStripe:
    """Stripe stub — real payment/verification needs STRIPE_API_KEY (+ webhook secret)."""

    def create_customer(self, **kw):
        raise NotImplementedError("Stripe not configured — needs STRIPE_API_KEY")

    def create_checkout_session(self, **kw):
        raise NotImplementedError("Stripe not configured — needs STRIPE_API_KEY")

    def construct_event(self, payload, sig, secret):
        raise NotImplementedError("Stripe not configured — needs STRIPE_API_KEY")


class _VerificationMailer:
    """The AccountService email seam, minting the REAL credential before delivery.

    `AccountService.create` calls `email.send_verification(email, account_id)` — the second
    positional is the ACCOUNT ID. This wrapper turns it into the signed single-use 15-minute
    token (EmailTokenService.issue) and hands THAT to the underlying sender (which composes the
    click-through link and is itself draft-gated). `send_welcome` passes straight through.
    """

    def __init__(self, sender, tokens):
        self.sender = sender   # ResendEmailSender or _Noop
        self.tokens = tokens   # EmailTokenService

    def send_verification(self, email, account_id):
        return self.sender.send_verification(email, self.tokens.issue(str(account_id)))

    def send_welcome(self, email, tenant_id=None):
        return self.sender.send_welcome(email, tenant_id)


class _VerifyingAccountService(AccountService):
    """AccountService that ALSO mints + delivers the SMS OTP at create time.

    The base `create` only triggers the email leg; the phone-verify step needs a live code too.
    Failure posture: an OTP problem (rate limit, SNS outage) never fails the signup request —
    the account exists, the email leg ran, and a re-submitted signup (idempotent create) re-issues
    a code within the OtpService send budget.
    """

    def __init__(self, store, cognito, email_sender, sms, *, otp=None):
        super().__init__(store, cognito, email_sender, sms)
        self.otp = otp  # OtpService | None (None = phone verification not configured)

    def create(self, account_id: str, email: str, phone: str):
        acct = super().create(account_id, email, phone)
        if self.otp is not None and not acct.phone_verified:
            try:
                self.sms.send_otp(acct.phone, self.otp.issue(acct.id))
            except OtpRateLimitError as e:
                log.info("OTP issue rate-limited for account %s: %s", acct.id, e)
            except Exception as e:  # noqa: BLE001 — SmsSendError etc.; signup must not 500
                log.warning("OTP delivery failed for account %s: %s: %s",
                            acct.id, type(e).__name__, e)
        return acct


class SfnProvisioningTrigger:
    """Starts the uplift-provisioning Step Functions execution — the DECOUPLED on_paid path.

    Replaces the synchronous in-process `provisioner.provision` inside the webhook request
    (TODO INT/P1 "Connect the Stripe webhook to start the SFN execution"): `start` returns as
    soon as StartExecution is accepted; the Lambda in `signup/lambda_handler.py` runs the
    idempotent steps. Selected by `build_signup_deps` ONLY when BOTH the SIGNUP_REAL_DEPS master
    switch AND the NEW `PROVISIONING_SFN_ARN` env (shared/config.py; infra/REQUESTS.md REQ-005)
    are set — the in-process path stays the default everywhere else.

    ORDERING (hard rule): this fires only AFTER the atomic stripe_events ledger claim — it is
    wired as PaymentService's `on_paid`, which `handle_webhook` invokes strictly after
    `_claim()` wins. EXACTLY-ONCE: the execution NAME is deterministic (account_id-derived), so
    a Stripe re-delivery that slips past the ledger (e.g. no ledger configured, or the
    different-event-id re-delivery) re-derives the SAME name and StartExecution answers
    ExecutionAlreadyExists — treated as a successful no-op, never a second pipeline.
    # VERIFY (SFN semantics): ExecutionAlreadyExists dedupes within the 90-day execution
    # history; a re-delivery older than that would start a fresh execution — safe anyway,
    # because every step is idempotent and an ACTIVE account short-circuits to skips.
    """

    def __init__(self, state_machine_arn: str, *, region: str | None = None, client=None):
        self._arn = state_machine_arn
        self._region = region
        self._client = client   # injected fake in tests; lazily built otherwise (import-safe)

    def _sfn(self):
        if self._client is None:
            import boto3  # noqa: PLC0415 — lazy: building deps must not require boto3/network
            self._client = boto3.client(
                "stepfunctions", region_name=self._region or load().aws_region
            )
        return self._client

    @staticmethod
    def execution_name(account_id: str, attempt: int = 0) -> str:
        """Deterministic, SFN-legal execution name (<=80 chars of [A-Za-z0-9_-]).

        attempt=0 is the webhook path: same account -> same name -> re-delivery no-ops.
        attempt>0 is the operator retry path (the failed run burned the base name).
        """
        safe = re.sub(r"[^A-Za-z0-9_-]", "-", str(account_id))[:60]
        return f"provision-{safe}" + (f"-r{attempt}" if attempt else "")

    def start(self, account, attempt: int = 0) -> dict:
        """The on_paid callback: start exactly one execution for this account."""
        name = self.execution_name(account.id, attempt)
        try:
            self._sfn().start_execution(
                stateMachineArn=self._arn, name=name,
                input=json.dumps({"account_id": str(account.id)}),
            )
        except Exception as e:  # noqa: BLE001 — only the already-exists dedupe is absorbed
            if not _is_execution_already_exists(e):
                raise
            log.info("provisioning execution %s already exists (re-delivery no-op)", name)
            return {"started": False, "execution": name, "reason": "already_exists"}
        return {"started": True, "execution": name}


def _is_execution_already_exists(exc: Exception) -> bool:
    """Match boto3's ExecutionAlreadyExists across the modeled-exception and error-code shapes."""
    if type(exc).__name__ == "ExecutionAlreadyExistsException" \
            or type(exc).__name__ == "ExecutionAlreadyExists":
        return True
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = (resp.get("Error") or {}).get("Code", "")
        return code in ("ExecutionAlreadyExists", "ExecutionAlreadyExistsException")
    return False


def _build_funnel(cfg) -> Funnel | None:
    """Env-guarded server-side PostHog funnel (TODO INT/P3) — None unless BOTH the
    SIGNUP_REAL_DEPS master switch AND the NEW POSTHOG_PROJECT_KEY_VALUE env (REQ-006;
    SM `friesenlabs/platform/shared/posthog-project-key` is the source) are set. The client is
    lazy (no network/thread at construction), fire-and-forget, and never raises — analytics can
    never fail a webhook or a provisioning step."""
    if cfg.signup_real_deps and cfg.posthog_project_key_value:
        return Funnel(PostHogClient(cfg.posthog_project_key_value, cfg.posthog_host))
    return None


def build_provisioner(workspace_store=None, *, store=None, cognito=None, email_sender=None,
                      refund=None, funnel=None) -> Provisioner:
    """Env-guarded Provisioner construction — ONE selection path for BOTH runtimes.

    `build_signup_deps` (the API task) passes its already-built store/cognito/email_sender (and
    its funnel, so payment + provisioning share ONE PostHog client) so the two planes share
    adapters + the Pg pool; `signup/lambda_handler.py` (the SFN Task runtime) calls it bare on
    cold start and gets the identical env-guarded selection — including the funnel and the
    tenant-defaults seeder, so the SFN path emits instance_provisioned/provisioning_failed and
    seeds tenant_settings exactly like the in-process path. The SIGNUP_REAL_DEPS master switch
    is honored exactly as in `build_signup_deps`: without it everything is the stub, regardless
    of what other env happens to be present.

    Step-5 seams, settled (TODO INT/P2 "tenant-context correctness"): `tenant_defaults` is the
    REAL `signup.tenant_defaults.PgTenantDefaults` whenever the crm_app DSN is configured under
    the switch (idempotent tenant_settings seed, SET LOCAL pattern); `cube` stays _Noop
    PERMANENTLY by design — Cube's security context is per-request JWT, nothing to provision
    (see `Provisioner._step_tenant_context`). Secrets-Manager / agent-plane seams remain _Noop
    (follow-up TODOs): with a real ANTHROPIC_ADMIN_KEY the # VERIFY'd key-create endpoint would
    fail -> Provisioner parks the account + archives the workspace (rollback-safe), so live
    provisioning stays BLOCKED: Lane Nick until those seams land. `refund=None` keeps the
    record-only terminal-failure stub (`signup.provisioning.refund_stub` — # VERIFY the Stripe
    refund endpoint there before injecting a live callback).
    """
    cfg = load()
    real = cfg.signup_real_deps
    dsn = dsn_from_env() if real else None
    if store is None:
        store = PgAccountStore(dsn) if dsn else _AccountStore()
    if cognito is None:
        cognito = (
            CognitoAdminClient(cfg.cognito_user_pool_id, region=cfg.aws_region)
            if real and cfg.cognito_user_pool_id else _StubCognito()
        )
    if email_sender is None:
        email_sender = (
            ResendEmailSender(
                cfg.resend_api_key,
                cfg.resend_from_email,
                allow_real_sends=cfg.allow_real_sends,   # draft-gate stands in the Lambda too
                verify_url_base=cfg.signup_verify_url_base,
            )
            if real and cfg.resend_api_key else _Noop()
        )
    anthropic_admin = (
        AnthropicAdminClient(cfg.anthropic_admin_key)
        if real and cfg.anthropic_admin_key else _Noop()
    )
    if funnel is None:
        funnel = _build_funnel(cfg)   # deterministic from cfg — the Lambda cold start lands here
    # The REAL step-5 db seam (tenant_settings seed) rides the same crm_app DSN guard as the
    # stores; None falls back to the Provisioner's `db` (_Noop here) — offline boots unchanged.
    tenant_defaults = PgTenantDefaults(dsn) if dsn else None
    return Provisioner(
        store=store, mint_tenant_id=lambda aid: str(uuid.uuid4()), db=_Noop(),
        anthropic_admin=anthropic_admin, secrets=_Noop(), cognito=cognito, cube=_Noop(),
        resend=email_sender, agent_plane=_Noop(), workspace_store=workspace_store,
        refund=refund, funnel=funnel, tenant_defaults=tenant_defaults,
    )


def build_signup_deps(workspace_store=None, *, now=time.time) -> SignupDeps:
    """`workspace_store` (optional `agents.workspace_store.WorkspaceStore`) persists the
    per-tenant Managed Agents ids at provisioning time; None (default) skips persistence
    (DB unconfigured). `now` is the clock injected into the token/OTP services (test seam)."""
    cfg = load()
    # THE MASTER SWITCH (module docstring): no real adapter — Stripe, Cognito admin, senders,
    # Anthropic admin, Pg stores, token services — is selected unless SIGNUP_REAL_DEPS is set
    # exactly 'true'/'1'. The env vars the per-adapter guards key off (COGNITO_USER_POOL_ID,
    # DB_*) are already present on the live API task for OTHER features; without this flag a
    # mere image deploy must boot byte-identically all-stub.
    real = cfg.signup_real_deps
    dsn = dsn_from_env() if real else None

    # --- stores: Aurora-backed when the crm_app DSN is configured (shared across the 2 Fargate
    # --- tasks + survives restarts); else the per-task in-memory fallback.
    if dsn:
        store = PgAccountStore(dsn)
        event_ledger = PgStripeEventLedger(dsn)
        otp_store = PgOtpStore(dsn)
    else:
        store = _AccountStore()
        event_ledger = None  # per-task account-state idempotency only
        otp_store = None     # OtpService falls back to its in-process store

    # --- identity plane ---
    cognito = (
        CognitoAdminClient(cfg.cognito_user_pool_id, region=cfg.aws_region)
        if real and cfg.cognito_user_pool_id else _StubCognito()
    )

    # --- outbound senders (BOTH refuse real delivery until ALLOW_REAL_SENDS=true) ---
    email_sender = (
        ResendEmailSender(
            cfg.resend_api_key,
            cfg.resend_from_email,
            allow_real_sends=cfg.allow_real_sends,
            verify_url_base=cfg.signup_verify_url_base,
        )
        if real and cfg.resend_api_key else _Noop()
    )
    sms_sender = SnsSmsOtpSender(cfg.aws_region, allow_real_sends=cfg.allow_real_sends)

    # --- verification credentials: issued at create, verified by the /verify-* endpoints ---
    if real and cfg.signup_token_secret_value:
        email_tokens = EmailTokenService(
            cfg.signup_token_secret_value,
            ttl_seconds=cfg.signup_email_token_ttl_s,
            now=now,
        )
        otp = OtpService(
            cfg.signup_token_secret_value,
            store=otp_store,
            ttl_seconds=cfg.signup_otp_ttl_s,
            max_attempts=cfg.signup_otp_max_attempts,
            max_sends=cfg.signup_otp_max_sends,
            send_window_seconds=cfg.signup_otp_send_window_s,
            now=now,
        )
        email_token_ok = email_tokens.verify
        sms_code_ok = otp.verify
        account_email = _VerificationMailer(email_sender, email_tokens)
    else:
        # Master switch off / no signing secret -> verification stays OFF (the safe pre-wire:
        # nothing can be minted OR verified, may_pay never flips, checkout 400s). The accounts
        # service gets a _Noop mailer — without a token there is nothing valid to email.
        otp = None
        email_token_ok = lambda aid, token: False  # noqa: E731
        sms_code_ok = lambda aid, code: False      # noqa: E731
        account_email = _Noop()

    accounts = _VerifyingAccountService(store, cognito, account_email, sms_sender, otp=otp)

    # --- server-side PostHog funnel (INT/P3): ONE client shared by payment (payment_succeeded
    # --- from the signed webhook) and provisioning (instance_provisioned / provisioning_failed,
    # --- grouped under the tenant). None when unconfigured — both planes no-op.
    funnel = _build_funnel(cfg)

    # --- provisioning pipeline — ONE construction path shared with the Lambda runtime
    # --- (build_provisioner): the API task hands over its already-built store/cognito/sender
    # --- (+ funnel) so both planes ride the same adapters + Pg pool. The _Noop seams + the
    # --- BLOCKED: Lane Nick posture are documented on build_provisioner.
    provisioner = build_provisioner(
        workspace_store, store=store, cognito=cognito, email_sender=email_sender, funnel=funnel
    )

    # --- payment plane ---
    stripe = (
        StripeAdapter(
            api_key=cfg.stripe_api_key,
            price_ids=stripe_price_ids(),
            success_url=cfg.stripe_success_url,
            cancel_url=cfg.stripe_cancel_url,
        )
        if real and cfg.stripe_api_key else _StubStripe()
    )
    # on_paid: the SFN trigger when BOTH the master switch and the NEW PROVISIONING_SFN_ARN env
    # are set (REQ-005) — handle_webhook takes the atomic ledger claim FIRST, then on_paid fires,
    # so the execution starts strictly claim-ordered. Default stays the in-process provisioner.
    if real and cfg.provisioning_sfn_arn:
        on_paid = SfnProvisioningTrigger(cfg.provisioning_sfn_arn, region=cfg.aws_region).start
    else:
        on_paid = provisioner.provision
    payment = PaymentService(stripe, accounts, on_paid=on_paid, funnel=funnel,
                             event_ledger=event_ledger)

    # --- retry-provision (INT/P2 closure): the route's two layered gates -------------------
    # 1) `retry_provision` is wired ONLY under the master switch — without SIGNUP_REAL_DEPS the
    #    route answers 404 (byte-identical posture to the route not existing).
    # 2) `claims_tenant` is THE TRUST RULE gate: the same Cognito JWKS verification the API's
    #    authed routes use (api/auth.py), built only when the pool + client id are configured —
    #    None keeps the route refusing (403 internal-only; the operator path is the direct
    #    Lambda 'retry' invoke, IAM-gated). The in-process retry is idempotent
    #    (Provisioner.retry: ACTIVE = skip, non-parked = structured refusal) — and stays
    #    in-process even when on_paid is the SFN trigger: it is the SAME idempotent pipeline
    #    the Lambda steps run, and the SFN-shaped operator retry remains available via
    #    SfnProvisioningTrigger.start(account, attempt>0) / the Lambda 'retry' invoke.
    retry_provision = (lambda account_id: provisioner.retry(store.get(account_id))) if real \
        else None
    claims_tenant = (
        make_current_tenant(CognitoJwtVerifier(
            pool_id=cfg.cognito_user_pool_id,
            client_id=cfg.cognito_client_id,
            region=cfg.aws_region,
        ))
        if real and cfg.cognito_user_pool_id and cfg.cognito_client_id else None
    )

    return SignupDeps(
        accounts=accounts,
        payment=payment,
        stripe_webhook_secret=cfg.stripe_webhook_secret,
        new_account_id=lambda: str(uuid.uuid4()),
        email_token_ok=email_token_ok,
        sms_code_ok=sms_code_ok,
        verify_redirect_url=cfg.signup_verify_url_base,
        retry_provision=retry_provision,
        claims_tenant=claims_tenant,
    )
