"""Unit: api/prod_deps.build_signup_deps — env-guarded REAL adapters with stub fallbacks.

Proves the wiring contract (TODO INT/P0s):
  * the SIGNUP_REAL_DEPS MASTER SWITCH (deploy invariance, adversarial finding HIGH): the live
    API task already injects COGNITO_USER_POOL_ID + DB_* for other features — without the
    deliberate switch those must select NOTHING real (all stubs, byte-identical boot);
  * every per-adapter guard, UNDER the switch, selects its real adapter only when present;
  * an unconfigured build is byte-identical to the old all-stub wiring (boots, verification OFF);
  * the verification stack drives create -> email-token -> verify-email -> OTP -> verify-phone ->
    may_pay=True on a FAKE CLOCK, with expiry/replay/rate-limit enforced;
  * the outbound senders stay draft-gated (ALLOW_REAL_SENDS unset) even with keys present.

Config note: `shared.config.Config` captures env defaults at import time, so tests pin the
config by patching `prod_deps.load` / `prod_deps.dsn_from_env` (never the process env).
"""
import pytest

import api.prod_deps as prod_deps
from shared.config import Config
from signup.anthropic_admin import AnthropicAdminClient
from signup.cognito_admin import CognitoAdminClient
from signup.resend_sender import ResendEmailSender
from signup.sms_sender import SnsSmsOtpSender
from signup.store_pg import PgAccountStore, PgOtpStore, PgStripeEventLedger
from signup.stripe_adapter import StripeAdapter
from signup.tokens import OTP_DIGITS


def _cfg(monkeypatch, dsn=None, **overrides):
    monkeypatch.setattr(prod_deps, "load", lambda: Config(**overrides))
    monkeypatch.setattr(prod_deps, "dsn_from_env", lambda: dsn)


class Clock:
    """Injectable fake clock (same shape as tests/unit/test_tokens.py)."""

    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class RecordingSender:
    """Captures what Resend/SNS would deliver (swapped in for the draft-gated senders)."""

    def __init__(self):
        self.verification = []  # (email, signed_token)
        self.welcome = []
        self.otps = []          # (phone, code)

    def send_verification(self, email, token):
        self.verification.append((email, token))
        return True

    def send_welcome(self, email, tenant_id=None):
        self.welcome.append((email, tenant_id))
        return True

    def send_otp(self, phone, code):
        self.otps.append((phone, code))
        return True


def _verifying_deps(monkeypatch, clock, **overrides):
    """Build deps with the token stack live + recorders on both delivery seams."""
    _cfg(monkeypatch, signup_real_deps=True,
         signup_token_secret_value="test-signing-secret", **overrides)
    deps = prod_deps.build_signup_deps(now=clock)
    rec = RecordingSender()
    deps.accounts.email.sender = rec  # the _VerificationMailer's delivery seam
    deps.accounts.sms = rec
    return deps, rec


# ---------------------------------------------------------------- env guards
@pytest.mark.unit
def test_unconfigured_build_is_all_stubs_and_boots(monkeypatch):
    _cfg(monkeypatch)
    deps = prod_deps.build_signup_deps()
    assert isinstance(deps.payment.stripe, prod_deps._StubStripe)
    assert isinstance(deps.accounts.cognito, prod_deps._StubCognito)
    assert isinstance(deps.accounts.email, prod_deps._Noop)
    assert isinstance(deps.accounts.sms, SnsSmsOtpSender)  # self-gating: logs + drops
    assert deps.accounts.otp is None
    assert deps.payment.event_ledger is None
    assert deps.verify_redirect_url == ""
    # Verification stays hardcoded OFF — may_pay can never flip (the safe pre-wire behavior).
    assert deps.email_token_ok("a", "anything") is False
    assert deps.sms_code_ok("a", "123456") is False
    # The in-memory account flow still works (the /healthz-bootable unconfigured deploy).
    acct = deps.accounts.create(deps.new_account_id(), "u@x.com", "+15555550100")
    assert acct.state.value == "created"


@pytest.mark.unit
def test_stripe_guard_selects_real_adapter(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_ID_STARTER", "price_123")
    _cfg(monkeypatch, signup_real_deps=True,
         stripe_api_key="sk_test_x", stripe_webhook_secret="whsec_x")
    deps = prod_deps.build_signup_deps()
    assert isinstance(deps.payment.stripe, StripeAdapter)
    assert deps.payment.stripe._price_ids == {"starter": "price_123"}
    assert deps.stripe_webhook_secret == "whsec_x"


@pytest.mark.unit
def test_cognito_guard_selects_real_client_everywhere(monkeypatch):
    _cfg(monkeypatch, signup_real_deps=True, cognito_user_pool_id="us-east-1_Pool")
    deps = prod_deps.build_signup_deps()
    provisioner = deps.payment.on_paid.__self__
    assert isinstance(deps.accounts.cognito, CognitoAdminClient)
    assert isinstance(provisioner.cognito, CognitoAdminClient)


@pytest.mark.unit
def test_resend_guard_selects_real_sender_still_draft_gated(monkeypatch):
    _cfg(monkeypatch, signup_real_deps=True,
         resend_api_key="re_x", resend_from_email="hello@uplift.example",
         signup_token_secret_value="sssh", signup_verify_url_base="https://app.example/verify")
    deps = prod_deps.build_signup_deps()
    mailer = deps.accounts.email
    assert isinstance(mailer, prod_deps._VerificationMailer)
    assert isinstance(mailer.sender, ResendEmailSender)
    # DRAFT-GATE: keys alone never enable delivery — ALLOW_REAL_SENDS stays false.
    assert mailer.sender.allow_real_sends is False
    assert deps.accounts.sms.allow_real_sends is False
    # The provisioner's welcome-email seam rides the same sender; the SPA base is threaded.
    assert deps.payment.on_paid.__self__.resend is mailer.sender
    assert deps.verify_redirect_url == "https://app.example/verify"


@pytest.mark.unit
def test_anthropic_admin_guard(monkeypatch):
    _cfg(monkeypatch, signup_real_deps=True, anthropic_admin_key="sk-ant-admin-x")
    provisioner = prod_deps.build_signup_deps().payment.on_paid.__self__
    assert isinstance(provisioner.admin, AnthropicAdminClient)
    assert provisioner.admin.admin_key == "sk-ant-admin-x"
    # The individual guard sits UNDER the master switch: switch on + no key is still the stub.
    _cfg(monkeypatch, signup_real_deps=True)
    assert isinstance(prod_deps.build_signup_deps().payment.on_paid.__self__.admin,
                      prod_deps._Noop)


@pytest.mark.unit
def test_dsn_guard_selects_aurora_backed_stores(monkeypatch):
    import psycopg2.pool

    class _FakePool:  # no DB in unit tests — the pool is the only construction-time touchpoint
        def getconn(self):
            raise AssertionError("no DB access expected in this test")

        def putconn(self, conn):
            pass

    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool",
                        lambda minc, maxc, dsn: _FakePool())
    _cfg(monkeypatch, dsn="postgresql://crm_app:x@db.example/uplift",
         signup_real_deps=True, signup_token_secret_value="sssh")
    deps = prod_deps.build_signup_deps()
    assert isinstance(deps.accounts.store, PgAccountStore)
    assert isinstance(deps.payment.event_ledger, PgStripeEventLedger)
    assert isinstance(deps.accounts.otp._store, PgOtpStore)  # OTP state shared across tasks
    # The pre-minted workspace-key pool (issue #152) rides the same dsn guard: provisioning
    # consumes Console-pre-minted keys, never the dead Admin-API key-create endpoint.
    from signup.key_pool import PgWorkspaceKeyPool
    provisioner = deps.payment.on_paid.__self__
    assert isinstance(provisioner.key_pool, PgWorkspaceKeyPool)


@pytest.mark.unit
def test_key_pool_absent_without_dsn(monkeypatch):
    _cfg(monkeypatch, signup_real_deps=True)
    deps = prod_deps.build_signup_deps()
    assert deps.payment.on_paid.__self__.key_pool is None


# ------------------------------------------------- the internal bypass (its own env switch)
@pytest.mark.unit
def test_internal_bypass_default_is_off(monkeypatch):
    _cfg(monkeypatch, signup_real_deps=True)
    deps = prod_deps.build_signup_deps()
    assert deps.internal_bypass_domains == frozenset()   # default EMPTY = feature off


@pytest.mark.unit
def test_internal_bypass_wired_from_config(monkeypatch):
    _cfg(monkeypatch, signup_real_deps=True,
         signup_internal_bypass_domains="friesenlabs.com, Example.io")
    deps = prod_deps.build_signup_deps()
    assert deps.internal_bypass_domains == frozenset({"friesenlabs.com", "example.io"})


# ------------------------------------------------- the SIGNUP_REAL_DEPS master switch
@pytest.mark.unit
def test_master_switch_absent_keeps_all_stubs_despite_live_env(monkeypatch):
    """THE deploy-invariance regression (adversarial finding, HIGH).

    The live API task ALREADY injects COGNITO_USER_POOL_ID (for JWKS) and DB_* (for the
    request-path stores) for other features — and here even the full credential set is present.
    Without the deliberate SIGNUP_REAL_DEPS master switch a mere image deploy must still select
    NOTHING real: no Cognito admin client, no Aurora-backed signup state (REQ-002 grants OPEN),
    no Stripe/Resend/Anthropic-admin, verification hardcoded OFF.
    """
    import psycopg2.pool

    def _no_pool(*a, **k):
        raise AssertionError("master switch off — no Pg pool may even be constructed")

    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool", _no_pool)
    _cfg(monkeypatch,
         dsn="postgresql://crm_app:x@db.example/uplift",  # DB_* present (already on the task)
         cognito_user_pool_id="us-east-1_Pool",           # present for JWKS already
         stripe_api_key="sk_live_x", stripe_webhook_secret="whsec_x",
         resend_api_key="re_x", resend_from_email="hello@uplift.example",
         anthropic_admin_key="sk-ant-admin-x",
         signup_token_secret_value="sssh")                # signup_real_deps deliberately ABSENT
    deps = prod_deps.build_signup_deps()
    provisioner = deps.payment.on_paid.__self__
    assert isinstance(deps.payment.stripe, prod_deps._StubStripe)
    assert isinstance(deps.accounts.cognito, prod_deps._StubCognito)
    assert isinstance(provisioner.cognito, prod_deps._StubCognito)
    assert isinstance(deps.accounts.email, prod_deps._Noop)
    assert isinstance(provisioner.admin, prod_deps._Noop)
    assert isinstance(deps.accounts.store, prod_deps._AccountStore)
    assert deps.payment.event_ledger is None
    assert deps.accounts.otp is None
    # Verification stays hardcoded OFF — exactly the unconfigured boot.
    assert deps.email_token_ok("a", "anything") is False
    assert deps.sms_code_ok("a", "123456") is False


@pytest.mark.unit
def test_master_switch_alone_selects_nothing_real(monkeypatch):
    """The switch is necessary, not sufficient: each per-adapter guard still applies under it."""
    _cfg(monkeypatch, signup_real_deps=True)
    deps = prod_deps.build_signup_deps()
    assert isinstance(deps.payment.stripe, prod_deps._StubStripe)
    assert isinstance(deps.accounts.cognito, prod_deps._StubCognito)
    assert isinstance(deps.accounts.email, prod_deps._Noop)
    assert isinstance(deps.accounts.store, prod_deps._AccountStore)
    assert deps.payment.event_ledger is None
    assert deps.accounts.otp is None
    assert deps.email_token_ok("a", "t") is False


@pytest.mark.unit
def test_master_switch_env_parsing_is_exact(monkeypatch):
    """Config.signup_real_deps flips ONLY on exactly 'true'/'1' (fail-closed on near-misses)."""
    from shared.config import _switch_env

    for junk in ("", "True", "TRUE", " true", "true ", "yes", "on", "0", "false", "2"):
        monkeypatch.setenv("SIGNUP_REAL_DEPS", junk)
        assert _switch_env("SIGNUP_REAL_DEPS") is False, junk
    monkeypatch.delenv("SIGNUP_REAL_DEPS")
    assert _switch_env("SIGNUP_REAL_DEPS") is False
    for ok in ("true", "1"):
        monkeypatch.setenv("SIGNUP_REAL_DEPS", ok)
        assert _switch_env("SIGNUP_REAL_DEPS") is True, ok


# ---------------------------------------------------------------- the fake-clock verify flow
@pytest.mark.unit
def test_fake_clock_full_verification_flow_to_may_pay(monkeypatch):
    clock = Clock()
    deps, rec = _verifying_deps(monkeypatch, clock)

    acct = deps.accounts.create("acct-1", "founder@acme.com", "+1 (555) 555-0100")
    assert acct.may_pay is False
    [(email, token)] = rec.verification
    assert email == "founder@acme.com"
    assert "." in token and token != "acct-1"  # the SIGNED credential, never the raw account id
    [(phone, code)] = rec.otps
    assert phone == "+15555550100" and len(code) == OTP_DIGITS and code.isdigit()

    clock.advance(60)
    # Wrong credentials flip nothing.
    assert deps.email_token_ok("acct-1", "garbage") is False
    wrong = "000000" if code != "000000" else "111111"
    assert deps.sms_code_ok("acct-1", wrong) is False
    assert deps.accounts.store.get("acct-1").may_pay is False

    # create -> email-token -> verify-email -> OTP -> verify-phone -> may_pay=True
    acct = deps.accounts.verify_email("acct-1", deps.email_token_ok("acct-1", token))
    assert acct.email_verified is True and acct.may_pay is False
    acct = deps.accounts.verify_phone("acct-1", deps.sms_code_ok("acct-1", code))
    assert acct.phone_verified is True and acct.may_pay is True

    # Both credentials are single-use: a replay verifies nothing.
    assert deps.email_token_ok("acct-1", token) is False
    assert deps.sms_code_ok("acct-1", code) is False


@pytest.mark.unit
def test_fake_clock_expired_credentials_rejected(monkeypatch):
    clock = Clock()
    deps, rec = _verifying_deps(monkeypatch, clock)
    deps.accounts.create("acct-1", "u@x.com", "+15555550100")
    [(_, token)] = rec.verification
    [(_, code)] = rec.otps
    clock.advance(901)  # email TTL 900s; OTP TTL 600s — both now expired
    assert deps.email_token_ok("acct-1", token) is False
    assert deps.sms_code_ok("acct-1", code) is False


@pytest.mark.unit
def test_resignup_reissues_otp_within_budget_and_never_raises(monkeypatch):
    clock = Clock()
    deps, rec = _verifying_deps(monkeypatch, clock)
    for _ in range(7):  # send budget is 5/window — the extra creates must drop, not 500
        deps.accounts.create("acct-1", "u@x.com", "+15555550100")
    assert len(rec.otps) == 5          # rate limit capped delivery
    assert len(rec.verification) == 1  # email leg ran once (create is idempotent by id)
