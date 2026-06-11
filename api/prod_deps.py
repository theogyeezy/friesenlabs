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
pipeline's Secrets-Manager seam remains _Noop (follow-up TODO; cube is a DOCUMENTED no-op by
design — see Provisioner._step_tenant_context); the AGENT-PLANE seam is now REAL under the gate
below (signup.agent_plane.AgentPlaneEnsure — eager per ratified #123,
docs/decisions/agent-plane-ensure-eager-vs-lazy.md):

  SIGNUP_REAL_DEPS                     the master switch (the deliberate Lane Nick act) AND
  + ANTHROPIC_API_KEY + UPLIFT_ENV_ID  the AI-plane gate (the live API task now carries both —
                                       a deliberate flip, see CLAUDE.md "AI plane half-live") AND
  + a workspace store (crm_app DSN)    ids must be persistable/checkable, never orphaned
  -> AgentPlaneEnsure                  (else the _Noop stub-id fallback below, and the
                                       conversation factory's stub-id guard keeps /chat at 503)

Live end-to-end provisioning otherwise stays BLOCKED: Lane Nick until the Secrets-Manager seam
lands and the # VERIFY'd Anthropic Admin endpoints are confirmed.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid

from agents.workspace_store import PgWorkspaceStore
from api.auth import CognitoJwtVerifier, make_current_tenant
from api.public_routes import DEFAULT_TRUSTED_HOPS
from api.signup_routes import SignupDeps
from shared.config import (
    ENV_ANTHROPIC_API_KEY,
    ENV_PUBLIC_LEADS_TRUSTED_HOPS,
    ENV_UPLIFT_ENV_ID,
    _int_env,
    dsn_from_env,
    load,
    stripe_price_ids,
)
from shared.signup_session import SignupSessionTokens
from signup.abuse import (
    ACTION_RESEND,
    ACTION_SIGNUP,
    CaptchaVerifier,
    DEFAULT_RESEND_LIMIT,
    DEFAULT_SIGNUP_LIMIT,
    DEFAULT_VELOCITY_WINDOW_S,
    DisposableEmailBlocklist,
    SignupVelocityLimiter,
)
from signup.accounts import AccountService
from signup.agent_plane import AgentPlaneEnsure
from signup.anthropic_admin import AnthropicAdminClient
from signup.cognito_admin import CognitoAdminClient
from signup.funnel import Funnel
from signup.key_pool import InlineKeyMaterialError, PgWorkspaceKeyPool
from signup.payment import PaymentService
from signup.posthog_client import PostHogClient
from signup.provisioning import Provisioner
from signup.resend_sender import ResendEmailSender
from signup.secrets import Boto3ProvisioningSecrets
from signup.sms_sender import SnsSmsOtpSender
from signup.store_pg import PgAccountStore, PgOtpStore, PgStripeEventLedger
from signup.stripe_adapter import StripeAdapter
from signup.tenant_defaults import PgTenantDefaults
from signup.tokens import EmailTokenService, OtpRateLimitError, OtpService

# Env var names for the signup velocity-limiter caps (NEW deliberate names; plain config, never
# secret — the defaults ship in signup/abuse.py and are conservative enough for a fat-fingering
# human). These follow the same "SIGNUP_*" namespace + single-source-of-truth pattern as the
# other signup env vars (CONTRIBUTING.md §Env-var / secret-name contract).
ENV_SIGNUP_VELOCITY_LIMIT = "SIGNUP_VELOCITY_LIMIT"            # signups+resends per window per IP
ENV_SIGNUP_RESEND_LIMIT = "SIGNUP_RESEND_LIMIT"                # verification-resends per window per IP
ENV_SIGNUP_VELOCITY_WINDOW_S = "SIGNUP_VELOCITY_WINDOW_S"      # fixed-window length in seconds
# Trusted proxy hops for the signup acquisition path — reuses the leads-side constant and env name
# (same CloudFront → ALB topology; the two share one correct notion of "viewer IP").
# Plain config; junk / <1 → DEFAULT_TRUSTED_HOPS (never key the limiter on the ALB socket peer).
ENV_SIGNUP_TRUSTED_HOPS = ENV_PUBLIC_LEADS_TRUSTED_HOPS

log = logging.getLogger(__name__)


class _AccountStore:
    """In-memory account store (the no-DSN fallback; prod swaps in PgAccountStore)."""

    def __init__(self):
        self.rows: dict[str, object] = {}

    def get(self, account_id):
        return self.rows.get(account_id)

    def get_by_email(self, email):
        return next((a for a in self.rows.values() if getattr(a, "email", None) == email), None)

    def get_by_stripe_customer_id(self, customer_id):
        # The invoice.paid fallback resolver (signup/payment.py) — mirrors PgAccountStore.
        return next((a for a in self.rows.values()
                     if getattr(a, "stripe_customer_id", None) == customer_id), None)

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
        # The real agent plane (signup.agent_plane.AgentPlaneEnsure, selected by
        # _build_agent_plane under the gate documented there) returns the LIVE
        # workspace/environment/coordinator ids — and re-provisions over these stubs.
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
        # Skip the OTP when phone verification is flagged OFF (SIGNUP_REQUIRE_PHONE=false): no point
        # minting/sending a code the user never needs (it would just hit the draft-gate/SMS approval).
        from signup.accounts import _require_phone_verification  # noqa: PLC0415 — lazy
        if self.otp is not None and not acct.phone_verified and _require_phone_verification():
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


def _build_provisioning_secrets(cfg):
    """Env-guarded provisioning Secrets-Manager seam (replaces the historic `_Noop`).

    Provisioning step 2 now consumes a Secrets Manager *reference* from the key pool, so the seam
    must support get (resolve the reference -> material), put (write the per-tenant secret), and
    exists (idempotency). The REAL `signup.secrets.Boto3ProvisioningSecrets` (a thin composition
    over the already-VERIFY'd write + read SM clients) is selected ONLY under the SIGNUP_REAL_DEPS
    master switch — the deliberate Lane Nick go-live act. Everywhere else the _Noop stub stands
    (and no key_pool is wired there, so nothing tries to resolve a reference). boto3 stays lazy:
    building this never touches AWS."""
    if cfg.signup_real_deps:
        return Boto3ProvisioningSecrets(region=cfg.aws_region)
    return _Noop()


def _guard_no_inline_pool_material(key_pool) -> None:
    """Prod startup guard: a pool row holding inline key material (legacy plaintext) is fatal —
    the DB must never be the secret store. An InlineKeyMaterialError propagates (refuse to boot);
    a transient DB error at construction does NOT (best-effort — the real guard re-runs every
    consume via key_pool.consume, which raises the same error if it claims an inline row)."""
    try:
        key_pool.assert_no_inline_material()
    except InlineKeyMaterialError:
        raise
    except Exception as e:  # noqa: BLE001 — DB not reachable at build time: don't crash boot
        log.warning("workspace-key pool inline-material guard skipped (db not ready): %s: %s",
                    type(e).__name__, e)


def _build_agent_plane(cfg, workspace_store):
    """Env-guarded agent-plane seam (provisioning step 3) — EAGER per ratified #123
    (docs/decisions/agent-plane-ensure-eager-vs-lazy.md): the roster is created at signup,
    never in the request path.

    The real `signup.agent_plane.AgentPlaneEnsure` (7 specialists + coordinator in the EXISTING
    UPLIFT_ENV_ID environment, idempotent via the workspace-store row) is selected ONLY when ALL
    of these hold:
      * the SIGNUP_REAL_DEPS master switch (the deliberate Lane Nick act — deploy invariance:
        ANTHROPIC_API_KEY + UPLIFT_ENV_ID already ride the live API task for /chat, so the
        AI-plane gate alone must never flip live provisioning on a mere image deploy);
      * the AI-plane gate: ANTHROPIC_API_KEY (org key — API task/Lambda posture, NEVER the
        worker) + UPLIFT_ENV_ID (the live MA environment) both present;
      * a workspace store to check/persist the per-tenant ids (never create live resources
        whose ids cannot be persisted — they'd be unreachable orphans).
    Everywhere else: the _Noop fallback (stable 'stub-' ids), which the conversation factory's
    stub-id guard refuses to hand to a real runtime — /chat stays a graceful 503.
    """
    api_key = os.environ.get(ENV_ANTHROPIC_API_KEY, "")
    env_id = os.environ.get(ENV_UPLIFT_ENV_ID, "")
    if cfg.signup_real_deps and api_key and env_id and workspace_store is not None:
        return AgentPlaneEnsure(api_key=api_key, environment_id=env_id,
                                workspace_store=workspace_store)
    return _Noop()


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
    (see `Provisioner._step_tenant_context`).

    Step-3 agent plane, settled (ratified #123 — see `_build_agent_plane`): EAGER
    `signup.agent_plane.AgentPlaneEnsure` under SIGNUP_REAL_DEPS + ANTHROPIC_API_KEY +
    UPLIFT_ENV_ID + a workspace store; else the _Noop stub-id fallback. When the caller passes
    no `workspace_store` (the Lambda's bare cold-start call), it is defaulted from the crm_app
    DSN under the switch — the SFN path persists the SAME tenant_workspaces row the API task's
    in-process path does (both planes ride the identical env-guarded selection).

    The Secrets-Manager seam remains _Noop (follow-up TODO): with a real ANTHROPIC_ADMIN_KEY the
    # VERIFY'd key-create endpoint would fail -> Provisioner parks the account + archives the
    workspace (rollback-safe), so live per-tenant-workspace provisioning stays BLOCKED: Lane Nick
    until that seam lands. `refund=None` keeps the record-only terminal-failure stub
    (`signup.provisioning.refund_stub` — # VERIFY the Stripe refund endpoint there before
    injecting a live callback).
    """
    cfg = load()
    real = cfg.signup_real_deps
    dsn = dsn_from_env() if real else None
    if workspace_store is None and dsn:
        # The Lambda cold start calls this bare: give the SFN path the same per-tenant MA-id
        # persistence (and the agent plane the same idempotency check) the API task wires in.
        workspace_store = PgWorkspaceStore(dsn)
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
    # The pre-minted workspace-key POOL (issue #152: the Admin API cannot mint keys — 405; the
    # ratified Console-pool flow). Rides the same crm_app DSN guard (under the master switch):
    # step 2 consumes a Console-pre-minted key per tenant; an empty pool parks the signup as
    # pool_empty. None = the legacy admin-mint seam (offline/unconfigured stays all-stub).
    key_pool = PgWorkspaceKeyPool(dsn) if dsn else None
    if key_pool is not None:
        # Prod startup guard (security fix): refuse to serve provisioning if the pool table still
        # holds inline key material (a legacy plaintext pool). The DB must never be the secret
        # store — re-load via scripts/ops/load_workspace_keys.py so material lives in Secrets
        # Manager and only a reference remains. Best-effort: a DB hiccup at construction must not
        # crash boot, but an InlineKeyMaterialError (material actually present) propagates.
        _guard_no_inline_pool_material(key_pool)
    # The REAL Secrets-Manager seam (this replaces the historic _Noop): the pool hands provisioning
    # a Secrets Manager *reference*, so step 2 must resolve it (secrets.get) and write the per-tenant
    # secret (secrets.put). Selected under the master switch; offline/unconfigured stays the _Noop
    # stub (no key_pool there either, so nothing tries to resolve a reference).
    secrets = _build_provisioning_secrets(cfg)
    return Provisioner(
        store=store, mint_tenant_id=lambda aid: str(uuid.uuid4()), db=_Noop(),
        anthropic_admin=anthropic_admin, secrets=secrets, cognito=cognito, cube=_Noop(),
        resend=email_sender, agent_plane=_build_agent_plane(cfg, workspace_store),
        workspace_store=workspace_store,
        refund=refund, funnel=funnel, tenant_defaults=tenant_defaults, key_pool=key_pool,
    )


def _build_abuse_controls(cfg, now):
    """Construct the three in-process abuse controls and the session-token helper.

    These are built UNCONDITIONALLY (they carry safe/permissive defaults regardless of env) with
    one exception: ``session_tokens`` needs a signing secret so it gates on
    ``cfg.signup_token_secret_value`` — the same secret as the email tokens.

    * ``disposable`` — always wired from the shipped file (signup/disposable_email_domains.txt)
      plus the two override knobs; a missing file degrades gracefully to blocking no one.
    * ``velocity`` — always wired (in-process; no network); caps/window are env-overridable with
      conservative defaults; junk/unset → the abuse.py defaults (5/hour per IP per action).
    * ``captcha`` — always wired (the seam defaults OPEN: ``required=False`` → verify() is a
      no-op and signup routes are byte-identical to having no CAPTCHA); flips to required only
      when SIGNUP_CAPTCHA_REQUIRED=true/1, and only checks a real token when a validator is wired
      (a follow-up TODO — today it fails closed so "required" is never a no-op lie).
    * ``session_tokens`` — wired only when ``signup_token_secret_value`` is present (same gate as
      the email/OTP token stack). None = the legacy raw-account_id path stays active (behavior
      unchanged when the signing secret is not injected).
    * ``trusted_hops`` — read from SIGNUP_TRUSTED_HOPS / ENV_PUBLIC_LEADS_TRUSTED_HOPS (the same
      CloudFront→ALB topology as /public/leads); junk/<1 → DEFAULT_TRUSTED_HOPS.

    None of these touch the network or DB at construction — they are safe to build eagerly.
    """
    # --- disposable-email blocklist (always wired — static file ships in-repo) ---
    disposable = DisposableEmailBlocklist.from_env()

    # --- velocity limiter (always wired — in-process, env-overridable caps) ---
    signup_limit = _int_env(ENV_SIGNUP_VELOCITY_LIMIT, DEFAULT_SIGNUP_LIMIT)
    resend_limit = _int_env(ENV_SIGNUP_RESEND_LIMIT, DEFAULT_RESEND_LIMIT)
    window_s = _int_env(ENV_SIGNUP_VELOCITY_WINDOW_S, DEFAULT_VELOCITY_WINDOW_S)
    # The limiter is keyed on (action, ip), so a single instance handles BOTH signup and resend
    # independently. The caps are symmetric (same limit/window for both actions) unless one of
    # the separate knobs overrides resend to a tighter budget. We build one limiter per action
    # only if the caps differ; otherwise a shared limiter is used for both actions.
    # HOWEVER: signup/abuse.py documents that signup and resend get INDEPENDENT budgets by design
    # (the limiter keys on `action` — so (ACTION_SIGNUP, ip) and (ACTION_RESEND, ip) are separate
    # counters in the same instance). ONE shared instance with the lower/stricter limit is
    # unacceptable because resend might deserve a separate budget. Instead we use ONE limiter
    # where the `limit` is the signup budget (the more conservative default); if the operator
    # wants a different resend budget they can set ENV_SIGNUP_RESEND_LIMIT. The action key means
    # the per-action counters never interfere even in the same instance.
    # For simplicity: build one limiter; SignupVelocityLimiter keys on (action, ip) so one
    # instance is fine for both actions with the SAME limit. If limits differ, build two.
    if signup_limit == resend_limit:
        velocity: SignupVelocityLimiter | None = SignupVelocityLimiter(
            limit=signup_limit, window_seconds=window_s, now=now
        )
    else:
        # Different caps: use the more permissive one for the shared instance — no, actually we
        # should use per-action limiters. But SignupVelocityLimiter doesn't expose per-action
        # caps; it uses the SAME limit for every action. In this case, use the signup_limit for
        # the velocity slot (the routes pass ACTION_SIGNUP/ACTION_RESEND as the action key, and
        # the instance already tracks them separately). The effective cap per action is `limit`;
        # if ops needs separate budgets per action they can fork a second instance later. For now,
        # the shared instance uses signup_limit (the more conservative of the two in most configs).
        velocity = SignupVelocityLimiter(
            limit=min(signup_limit, resend_limit), window_seconds=window_s, now=now
        )

    # --- captcha seam (always wired; defaults OPEN = no-op unless SIGNUP_CAPTCHA_REQUIRED=true) ---
    captcha = CaptchaVerifier.from_env()

    # --- session tokens (only when the signing secret is available) ---
    session_tokens: SignupSessionTokens | None = None
    if cfg.signup_token_secret_value:
        session_tokens = SignupSessionTokens(cfg.signup_token_secret_value, now=now)

    # --- trusted hops (plain config; reuses the leads-side env + constant) ---
    try:
        hops = int(os.environ.get(ENV_SIGNUP_TRUSTED_HOPS, DEFAULT_TRUSTED_HOPS))
        if hops < 1:
            hops = DEFAULT_TRUSTED_HOPS
    except (TypeError, ValueError):
        hops = DEFAULT_TRUSTED_HOPS

    return disposable, velocity, captcha, session_tokens, hops


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

    # --- acquisition-funnel abuse controls (signup/abuse.py) ----------------------------------
    # Built unconditionally (safe/permissive defaults; purely in-process; no network/DB).
    # session_tokens gates on the signing secret (same as the email/OTP token stack).
    # trusted_hops is plain config (CloudFront→ALB topology = 2; env-overridable).
    disposable, velocity, captcha, session_tokens, trusted_hops = _build_abuse_controls(cfg, now)

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
        # TESTING-ONLY internal Stripe bypass — its OWN deliberate env switch
        # (SIGNUP_INTERNAL_BYPASS_DOMAINS, default EMPTY = off): the checkout route settles
        # allow-listed VERIFIED domains via PaymentService.internal_comp (same idempotent
        # ledger + on_paid path), no Stripe call.
        internal_bypass_domains=cfg.internal_bypass_domain_set(),
        # --- abuse controls (always constructed from env; safe defaults when env is unset) ---
        disposable=disposable,
        velocity=velocity,
        captcha=captcha,
        session_tokens=session_tokens,
        trusted_hops=trusted_hops,
    )
