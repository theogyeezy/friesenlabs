"""Unit: the SFN provisioning Lambda handler + the StartExecution trigger (TODO INT/P1+P2).

Proves the four properties the SFN decoupling hangs on:
  * STEP IDEMPOTENCY — every `{account_id, step}` Task invocation is check-then-create, so the
    machine's Retry policy (and a whole duplicate execution) re-runs without double-provisioning;
  * DETERMINISTIC NAMING — the trigger derives the execution name from the account_id, so a
    Stripe re-delivery re-derives the SAME name and ExecutionAlreadyExists is a no-op;
  * CLAIM ORDERING — the trigger fires only AFTER PaymentService's atomic ledger claim;
  * TERMINAL FAILURE — park_failed is a state-only flip that fires the injected refund seam
    AT MOST ONCE and never raises; the operator `retry` entrypoint re-provisions a parked
    account idempotently.
"""
import pytest

import api.prod_deps as prod_deps
import signup.lambda_handler as lambda_handler
from shared.config import Config
from signup.accounts import AccountService, State
# Bind the adapter classes at COLLECTION time (same as tests/unit/test_prod_deps.py):
# test_anthropic_admin/test_resend_sender importlib.reload their modules mid-run, and a
# function-local import after that would grab the NEW class object while prod_deps still
# holds the original — isinstance would then fail for identity, not behavior.
from signup.anthropic_admin import AnthropicAdminClient
from signup.cognito_admin import CognitoAdminClient
from signup.payment import PaymentService
from signup.provisioning import Provisioner
from signup.resend_sender import ResendEmailSender

# Reuse the provisioning fakes (the integration suite does the same).
from tests.unit.test_signup_provisioning import (
    AnthropicAdmin, Cognito, DB, Email, Recorder, Secrets, Store,
)

# The exact Task order infra/modules/provisioning/main.tf drives (build steps then Activate).
MACHINE_STEPS = ["tenant_record", "workspace", "agent_plane", "cognito_tenant",
                 "tenant_context", "welcome", "activate"]


# ---------------- fakes / helpers ----------------
class StripeFake:
    def __init__(self, event):
        self.event = event

    def construct_event(self, payload, sig, secret):
        if sig != "good-sig":
            raise ValueError("bad signature")
        return self.event

    def create_customer(self, email, idempotency_key):
        return {"id": "cus_1"}

    def create_checkout_session(self, **kw):
        return {"id": "cs_1"}


class OrderedLedger:
    """In-memory stripe_events ledger that records WHEN the claim was taken."""

    def __init__(self, order):
        self.order = order
        self.rows = {}

    def mark_handled(self, event_id, account_id=None):
        if event_id in self.rows:
            return False
        self.rows[event_id] = account_id
        self.order.append(("claim", event_id))
        return True

    def release(self, event_id):
        self.rows.pop(event_id, None)


class ExecutionAlreadyExists(Exception):
    """Mimics boto3's modeled stepfunctions exception (matched by class name)."""


class FakeSfnClient:
    def __init__(self, order=None, raise_already_exists=False):
        self.calls = []
        self.order = order
        self.raise_already_exists = raise_already_exists

    def start_execution(self, **kw):
        if self.raise_already_exists:
            raise ExecutionAlreadyExists(kw["name"])
        self.calls.append(kw)
        if self.order is not None:
            self.order.append(("start", kw["name"]))
        return {"executionArn": f"arn:aws:states:::execution:{kw['name']}"}


def _paid_account(aid="a1"):
    """A verified, PAID account in a fresh store (the state the webhook leaves behind)."""
    store = Store()
    svc = AccountService(store, Cognito(), Email(), Recorder())
    svc.create(aid, "u@x.com", "+15555550100")
    svc.verify_email(aid, True)
    svc.verify_phone(aid, True)
    acct = store.get(aid)
    acct.state = State.PAID
    return store, acct


def _provisioner(store, admin=None, refund=None):
    return Provisioner(
        store=store, mint_tenant_id=lambda aid: f"tenant-{aid}", db=DB(),
        anthropic_admin=admin or AnthropicAdmin(), secrets=Secrets(), cognito=Cognito(),
        cube=Recorder(), resend=Recorder(), agent_plane=Recorder(), refund=refund,
    )


def _wire_handler(monkeypatch, prov):
    """Inject the runtime the cold-start cache would hold (monkeypatch restores None after)."""
    monkeypatch.setattr(lambda_handler, "_PROVISIONER", prov)


def _cfg(monkeypatch, dsn=None, **overrides):
    monkeypatch.setattr(prod_deps, "load", lambda: Config(**overrides))
    monkeypatch.setattr(prod_deps, "dsn_from_env", lambda: dsn)


# ---------------- the handler drives the machine ----------------
@pytest.mark.unit
def test_handler_runs_machine_order_to_active(monkeypatch):
    store, acct = _paid_account()
    prov = _provisioner(store)
    _wire_handler(monkeypatch, prov)
    for step in MACHINE_STEPS:
        out = lambda_handler.handler({"account_id": "a1", "step": step})
        assert out["status"] == "ok", step
        assert out["account_id"] == "a1" and out["step"] == step  # structured for SFN choices
    assert store.get("a1").state is State.ACTIVE
    assert store.get("a1").tenant_id == "tenant-a1"


@pytest.mark.unit
def test_each_step_is_idempotent_when_rerun(monkeypatch):
    """The SFN Retry contract: re-running ANY Task must not double-provision anything."""
    store, acct = _paid_account()
    admin = AnthropicAdmin()
    prov = _provisioner(store, admin=admin)
    _wire_handler(monkeypatch, prov)
    for step in MACHINE_STEPS[:-1]:           # the build steps
        lambda_handler.handler({"account_id": "a1", "step": step})
        lambda_handler.handler({"account_id": "a1", "step": step})  # the retried delivery
    # One tenant, one workspace, one key — never two.
    assert prov.db.tenants == {"tenant-a1"}
    assert list(admin.workspaces.values()) == ["ws_1"]
    assert list(admin.keys.values()) == ["key_tenant-a1"]
    assert store.get("a1").tenant_id == "tenant-a1"
    # activate twice: the second is a structured skip, not a crash or a re-fire.
    assert lambda_handler.handler({"account_id": "a1", "step": "activate"})["status"] == "ok"
    out = lambda_handler.handler({"account_id": "a1", "step": "activate"})
    assert out["status"] == "skipped" and out["reason"] == "already_active"
    # ...and a whole duplicate execution against the now-ACTIVE account degrades to skips.
    out = lambda_handler.handler({"account_id": "a1", "step": "tenant_record"})
    assert out["status"] == "skipped" and out["reason"] == "already_active"
    assert store.get("a1").state is State.ACTIVE


@pytest.mark.unit
def test_step_failure_raises_for_sfn_retry_then_park_flips_state(monkeypatch):
    store, acct = _paid_account()
    admin = AnthropicAdmin(fail_on_key=True)
    prov = _provisioner(store, admin=admin)
    _wire_handler(monkeypatch, prov)
    lambda_handler.handler({"account_id": "a1", "step": "tenant_record"})
    # The failing build step RAISES — Step Functions owns the Retry policy.
    with pytest.raises(RuntimeError, match="key creation failed"):
        lambda_handler.handler({"account_id": "a1", "step": "workspace"})
    assert store.get("a1").state is State.PROVISIONING  # no in-handler park on a retryable step
    # The Catch-all routes to park_failed: a state-only flip that never raises.
    out = lambda_handler.handler({"account_id": "a1", "step": "park_failed"})
    assert out["status"] == "ok" and out["state"] == "provisioning_failed"
    assert store.get("a1").state is State.PROVISIONING_FAILED
    assert "provisioning_error" in store.get("a1").meta


@pytest.mark.unit
def test_handler_rejects_malformed_events_and_unknown_accounts(monkeypatch):
    store, _ = _paid_account()
    _wire_handler(monkeypatch, _provisioner(store))
    with pytest.raises(ValueError, match="account_id and step"):
        lambda_handler.handler({"step": "tenant_record"})
    with pytest.raises(ValueError, match="account_id and step"):
        lambda_handler.handler({"account_id": "a1"})
    with pytest.raises(ValueError, match="no such account"):
        lambda_handler.handler({"account_id": "ghost", "step": "tenant_record"})
    with pytest.raises(ValueError, match="unknown provisioning step"):
        lambda_handler.handler({"account_id": "a1", "step": "drop_tables"})


@pytest.mark.unit
def test_activate_never_short_circuits_payment(monkeypatch):
    """`activate` is a state-only flip, NOT a backdoor: a verified-but-unpaid account refuses."""
    store, acct = _paid_account()
    acct.state = State.PHONE_VERIFIED   # verified, never paid
    _wire_handler(monkeypatch, _provisioner(store))
    with pytest.raises(ValueError, match="cannot activate"):
        lambda_handler.handler({"account_id": "a1", "step": "activate"})
    assert store.get("a1").state is State.PHONE_VERIFIED


# ---------------- terminal failure: the refund seam ----------------
@pytest.mark.unit
def test_park_failed_fires_injected_refund_exactly_once(monkeypatch):
    refunds = []
    store, acct = _paid_account()
    prov = _provisioner(store, refund=lambda a: refunds.append(a.id) or "refund_queued")
    _wire_handler(monkeypatch, prov)
    out = lambda_handler.handler({"account_id": "a1", "step": "park_failed"})
    assert out["refund"] == "refund_queued"
    # A re-delivered/retried park (or a later second park) never double-refunds.
    out2 = lambda_handler.handler({"account_id": "a1", "step": "park_failed"})
    assert out2["refund"] == "already_requested"
    assert refunds == ["a1"]
    assert store.get("a1").meta["refund_requested"] is True


@pytest.mark.unit
def test_park_failed_default_refund_is_the_record_only_stub():
    # No injected callback: the # VERIFY'd stub records the need and moves no money.
    store, acct = _paid_account()
    out = _provisioner(store).park_failed(acct, error="RuntimeError: boom")
    assert out["status"] == "ok" and out["refund"] == "stub_recorded"
    assert acct.meta["provisioning_error"] == "RuntimeError: boom"


@pytest.mark.unit
def test_park_failed_survives_a_raising_refund_callback():
    """park_failed is the SFN Catch-all's terminal state — it must NEVER raise."""
    def boom(account):
        raise RuntimeError("stripe down")

    store, acct = _paid_account()
    out = _provisioner(store, refund=boom).park_failed(acct, error="x")
    assert out["status"] == "ok" and out["refund"] == "error"
    assert store.get("a1").state is State.PROVISIONING_FAILED
    assert "stripe down" in acct.meta["refund_error"]


@pytest.mark.unit
def test_inprocess_terminal_failure_also_rides_the_refund_seam():
    # The in-process provision() failure parks through the SAME park_failed (one code path).
    refunds = []
    store, acct = _paid_account()
    prov = _provisioner(store, admin=AnthropicAdmin(fail_on_key=True),
                        refund=lambda a: refunds.append(a.id) or "refund_queued")
    res = prov.provision(acct)
    assert res.ok is False and res.failed_step == "workspace"
    assert refunds == ["a1"]
    assert store.get("a1").state is State.PROVISIONING_FAILED


# ---------------- the operator retry entrypoint ----------------
@pytest.mark.unit
def test_retry_reprovisions_a_parked_account_to_active(monkeypatch):
    store, acct = _paid_account()
    admin = AnthropicAdmin(fail_on_key=True)
    prov = _provisioner(store, admin=admin)
    _wire_handler(monkeypatch, prov)
    assert prov.provision(acct).ok is False           # parked
    assert store.get("a1").state is State.PROVISIONING_FAILED
    admin.fail_on_key = False                          # the transient cause is fixed
    out = lambda_handler.handler({"account_id": "a1", "step": "retry"})
    assert out["status"] == "ok" and out["state"] == "active"
    assert store.get("a1").state is State.ACTIVE
    assert store.get("a1").tenant_id == "tenant-a1"   # the SAME tenant_id, not a second mint


@pytest.mark.unit
def test_retry_is_idempotent_and_refuses_non_parked_states(monkeypatch):
    store, acct = _paid_account()
    prov = _provisioner(store)
    _wire_handler(monkeypatch, prov)
    # Not parked (PAID): refused with a structured reason — never a stealth re-provision.
    out = lambda_handler.handler({"account_id": "a1", "step": "retry"})
    assert out["status"] == "refused" and "paid" in out["reason"]
    # ACTIVE: a skip (idempotent re-invocation).
    prov.provision(acct)
    out = lambda_handler.handler({"account_id": "a1", "step": "retry"})
    assert out["status"] == "skipped" and out["reason"] == "already_active"
    assert store.get("a1").state is State.ACTIVE


# ---------------- cold start honors the master switch ----------------
@pytest.mark.unit
def test_lambda_cold_start_is_all_stub_without_master_switch(monkeypatch):
    """Deploy invariance reaches the Lambda too: full env present, switch absent -> all stubs."""
    import psycopg2.pool

    def _no_pool(*a, **k):
        raise AssertionError("master switch off — no Pg pool may even be constructed")

    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool", _no_pool)
    _cfg(monkeypatch, dsn="postgresql://crm_app:x@db.example/uplift",
         cognito_user_pool_id="us-east-1_Pool", resend_api_key="re_x",
         anthropic_admin_key="sk-ant-admin-x")        # signup_real_deps deliberately ABSENT
    monkeypatch.setattr(lambda_handler, "_PROVISIONER", None)
    prov = lambda_handler._get_provisioner()
    assert isinstance(prov.store, prod_deps._AccountStore)
    assert isinstance(prov.cognito, prod_deps._StubCognito)
    assert isinstance(prov.admin, prod_deps._Noop)
    assert isinstance(prov.resend, prod_deps._Noop)


@pytest.mark.unit
def test_build_provisioner_selects_real_adapters_under_the_switch(monkeypatch):
    _cfg(monkeypatch, signup_real_deps=True, cognito_user_pool_id="us-east-1_Pool",
         resend_api_key="re_x", resend_from_email="hello@uplift.example",
         anthropic_admin_key="sk-ant-admin-x")
    prov = prod_deps.build_provisioner()
    assert isinstance(prov.cognito, CognitoAdminClient)
    assert isinstance(prov.admin, AnthropicAdminClient)
    assert isinstance(prov.resend, ResendEmailSender)
    assert prov.resend.allow_real_sends is False      # the draft-gate rides along
    assert isinstance(prov.store, prod_deps._AccountStore)  # no DSN -> in-memory


@pytest.mark.unit
def test_build_signup_deps_shares_its_adapters_with_the_provisioner(monkeypatch):
    _cfg(monkeypatch, signup_real_deps=True, cognito_user_pool_id="us-east-1_Pool")
    deps = prod_deps.build_signup_deps()
    provisioner = deps.payment.on_paid.__self__
    # One store + one cognito client across the signup AND provisioning planes (no second pool).
    assert provisioner.store is deps.accounts.store
    assert provisioner.cognito is deps.accounts.cognito


# ---------------- the SFN trigger ----------------
@pytest.mark.unit
def test_execution_name_is_deterministic_and_sfn_legal():
    n1 = prod_deps.SfnProvisioningTrigger.execution_name("acct-123")
    assert n1 == prod_deps.SfnProvisioningTrigger.execution_name("acct-123")  # deterministic
    assert n1 == "provision-acct-123"
    # Illegal chars sanitized, length bounded (SFN: <=80 of [A-Za-z0-9_-]).
    weird = prod_deps.SfnProvisioningTrigger.execution_name("a b/c@d" + "x" * 100)
    assert len(weird) <= 80
    assert all(c.isalnum() or c in "-_" for c in weird)
    # The retry path uses a distinct, attempt-suffixed name (the base name is burned).
    assert prod_deps.SfnProvisioningTrigger.execution_name("acct-123", attempt=2).endswith("-r2")


@pytest.mark.unit
def test_trigger_starts_one_execution_with_the_account_input():
    client = FakeSfnClient()
    trigger = prod_deps.SfnProvisioningTrigger("arn:aws:states:us-east-1:1:stateMachine:p",
                                               client=client)
    store, acct = _paid_account()
    out = trigger.start(acct)
    assert out == {"started": True, "execution": "provision-a1"}
    [call] = client.calls
    assert call["stateMachineArn"].endswith(":stateMachine:p")
    assert call["name"] == "provision-a1"
    assert call["input"] == '{"account_id": "a1"}'    # the machine's $.account_id


@pytest.mark.unit
def test_redelivery_already_exists_is_a_noop_not_an_error():
    trigger = prod_deps.SfnProvisioningTrigger(
        "arn:sm", client=FakeSfnClient(raise_already_exists=True))
    store, acct = _paid_account()
    out = trigger.start(acct)
    assert out == {"started": False, "execution": "provision-a1", "reason": "already_exists"}

    # The botocore error-code shape (a generic ClientError) is matched too.
    class ClientError(Exception):
        response = {"Error": {"Code": "ExecutionAlreadyExists"}}

    class CodeShapedClient:
        def start_execution(self, **kw):
            raise ClientError()

    out = prod_deps.SfnProvisioningTrigger("arn:sm", client=CodeShapedClient()).start(acct)
    assert out["started"] is False and out["reason"] == "already_exists"
    # Any OTHER failure still raises (handle_webhook releases the claim; Stripe retries).
    class Boom:
        def start_execution(self, **kw):
            raise RuntimeError("throttled")

    with pytest.raises(RuntimeError):
        prod_deps.SfnProvisioningTrigger("arn:sm", client=Boom()).start(acct)


@pytest.mark.unit
def test_trigger_fires_only_after_the_atomic_claim():
    """The hard ordering rule: ledger claim FIRST, StartExecution second — never the reverse."""
    order = []
    client = FakeSfnClient(order=order)
    trigger = prod_deps.SfnProvisioningTrigger("arn:sm", client=client)
    store, acct = _paid_account()
    svc = AccountService(store, Cognito(), Email(), Recorder())
    acct.state = State.PHONE_VERIFIED   # the pre-payment state the webhook advances
    event = {"id": "evt_1", "type": "checkout.session.completed",
             "data": {"object": {"client_reference_id": "a1"}}}
    pay = PaymentService(StripeFake(event), svc, on_paid=trigger.start,
                         event_ledger=OrderedLedger(order))
    assert pay.handle_webhook(b"{}", "good-sig", "whsec") == {
        "handled": True, "account_id": "a1",
    }
    assert order == [("claim", "evt_1"), ("start", "provision-a1")]
    # The re-delivered event loses the claim and never reaches StartExecution.
    pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert order == [("claim", "evt_1"), ("start", "provision-a1")]
    assert len(client.calls) == 1


@pytest.mark.unit
def test_trigger_selected_only_when_switch_and_arn_are_both_set(monkeypatch):
    arn = "arn:aws:states:us-east-1:1:stateMachine:uplift-provisioning"
    # Both set -> the decoupled SFN path.
    _cfg(monkeypatch, signup_real_deps=True, provisioning_sfn_arn=arn)
    deps = prod_deps.build_signup_deps()
    assert isinstance(deps.payment.on_paid.__self__, prod_deps.SfnProvisioningTrigger)
    # ARN without the master switch -> in-process (deploy invariance).
    _cfg(monkeypatch, provisioning_sfn_arn=arn)
    deps = prod_deps.build_signup_deps()
    assert isinstance(deps.payment.on_paid.__self__, Provisioner)
    # Switch without the ARN -> in-process (the default path).
    _cfg(monkeypatch, signup_real_deps=True)
    deps = prod_deps.build_signup_deps()
    assert isinstance(deps.payment.on_paid.__self__, Provisioner)
