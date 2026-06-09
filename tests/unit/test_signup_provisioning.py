"""Unit: signup + payment-gated, idempotent, rollback-safe provisioning."""
import pytest

from signup.accounts import AccountService, State
from signup.payment import PaymentError, PaymentService
from signup.provisioning import Provisioner
from signup.funnel import Funnel


# ---------------- fakes ----------------
class Store:
    def __init__(self):
        self.rows = {}

    def get(self, aid):
        return self.rows.get(aid)

    def insert(self, acct):
        self.rows[acct.id] = acct

    def update(self, acct):
        self.rows[acct.id] = acct


class Cognito:
    def __init__(self):
        self.users = {}
        self.tenant_set = {}
        self.confirmed = set()
        self._n = 0

    def create_unconfirmed_user(self, email):
        self._n += 1
        sub = f"sub{self._n}"
        self.users[sub] = {"email": email, "tenant_id": None}
        return sub

    def set_tenant_id(self, sub, tenant_id):
        self.tenant_set[sub] = tenant_id

    def confirm(self, sub):
        self.confirmed.add(sub)


class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def f(*a, **k):
            self.calls.append((name, a, k))
        return f


class Email(Recorder):
    pass


class AnthropicAdmin:
    def __init__(self, fail_on_key=False):
        self.workspaces = {}
        self.keys = {}
        self.deleted = []
        self.fail_on_key = fail_on_key
        self._n = 0

    def ensure_workspace(self, tenant_id):
        if tenant_id not in self.workspaces:
            self._n += 1
            self.workspaces[tenant_id] = f"ws_{self._n}"
        return self.workspaces[tenant_id]

    def create_workspace_key(self, ws_id, tenant_id):
        if self.fail_on_key:
            raise RuntimeError("Admin API key creation failed")
        self.keys[ws_id] = f"key_{tenant_id}"
        return self.keys[ws_id]

    def set_limits(self, ws_id, tenant_id):
        pass

    def delete_workspace(self, ws_id):
        self.deleted.append(ws_id)


class Secrets:
    def __init__(self):
        self.kv = {}

    def exists(self, k):
        return k in self.kv

    def put(self, k, v):
        self.kv[k] = v


class DB(Recorder):
    def __init__(self):
        super().__init__()
        self.tenants = set()

    def upsert_tenant(self, tenant_id, account_id):
        self.tenants.add(tenant_id)


def _account_service():
    return AccountService(Store(), Cognito(), Email(), Recorder())


def _verified_account(svc, aid="a1"):
    acct = svc.create(aid, "u@x.com", "+15555550100")
    svc.verify_email(aid, True)
    svc.verify_phone(aid, True)
    return svc.store.get(aid)


def _provisioner(store, admin=None):
    return Provisioner(
        store=store, mint_tenant_id=lambda aid: f"tenant-{aid}", db=DB(),
        anthropic_admin=admin or AnthropicAdmin(), secrets=Secrets(), cognito=Cognito(),
        cube=Recorder(), resend=Recorder(), agent_plane=Recorder(),
    )


# ---------------- verify before pay ----------------
@pytest.mark.unit
def test_cannot_pay_before_verified():
    svc = _account_service()
    svc.create("a1", "u@x.com", "+1555")
    pay = PaymentService(stripe=Recorder(), accounts=svc, on_paid=lambda a: None)
    with pytest.raises(PaymentError):
        pay.start_checkout("a1", "pro", "idem1")


@pytest.mark.unit
def test_create_is_idempotent():
    svc = _account_service()
    a = svc.create("a1", "u@x.com", "+1555")
    b = svc.create("a1", "u@x.com", "+1555")
    assert a is b  # same account, no duplicate Cognito user


# ---------------- provisioning only on signed webhook ----------------
class Stripe:
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


@pytest.mark.unit
def test_provisioning_only_triggers_on_signed_webhook():
    svc = _account_service()
    _verified_account(svc)
    provisioned = []
    event = {"type": "checkout.session.completed", "data": {"object": {"client_reference_id": "a1"}}}
    pay = PaymentService(Stripe(event), svc, on_paid=lambda a: provisioned.append(a.id))

    # A bad signature never provisions.
    with pytest.raises(ValueError):
        pay.handle_webhook(b"{}", "bad-sig", "whsec")
    assert provisioned == []

    # The signed webhook triggers provisioning exactly once.
    pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert provisioned == ["a1"]


@pytest.mark.unit
def test_redelivered_webhook_is_idempotent_no_double_provision():
    svc = _account_service()
    _verified_account(svc)
    provisioned = []
    event = {"type": "checkout.session.completed", "data": {"object": {"client_reference_id": "a1"}}}
    pay = PaymentService(Stripe(event), svc, on_paid=lambda a: provisioned.append(a.id))
    pay.handle_webhook(b"{}", "good-sig", "whsec")
    pay.handle_webhook(b"{}", "good-sig", "whsec")  # re-delivered
    assert provisioned == ["a1"]  # only once


# ---------------- provisioning idempotency + rollback ----------------
@pytest.mark.unit
def test_full_provisioning_sets_tenant_and_activates():
    svc = _account_service()
    acct = _verified_account(svc)
    acct.state = State.PAID
    prov = _provisioner(svc.store)
    res = prov.provision(acct)
    assert res.ok and res.tenant_id == "tenant-a1"
    assert svc.store.get("a1").state is State.ACTIVE
    # tenant_id was minted at provisioning, not before.
    assert acct.tenant_id == "tenant-a1"


@pytest.mark.unit
def test_provisioning_is_idempotent_when_rerun():
    svc = _account_service()
    acct = _verified_account(svc)
    acct.state = State.PAID
    prov = _provisioner(svc.store)
    prov.provision(acct)
    res2 = prov.provision(acct)  # already ACTIVE
    assert res2.ok and "already_active" in res2.steps_done


@pytest.mark.unit
def test_rollback_on_midfailure_parks_failed_and_tears_down_workspace():
    svc = _account_service()
    acct = _verified_account(svc)
    acct.state = State.PAID
    admin = AnthropicAdmin(fail_on_key=True)  # fail during step 2 (after workspace created)
    prov = _provisioner(svc.store, admin=admin)
    res = prov.provision(acct)
    assert res.ok is False
    assert res.failed_step == "workspace"
    assert svc.store.get("a1").state is State.PROVISIONING_FAILED
    # the half-created workspace was rolled back (no orphan)
    assert admin.deleted == list(admin.workspaces.values())
    assert "provisioning_error" in acct.meta


# ---------------- M6: signed webhook for an unknown account is a handled no-op ----------------
@pytest.mark.unit
def test_webhook_unknown_account_is_handled_noop_not_crash():
    svc = _account_service()
    _verified_account(svc)  # the only real account is "a1"
    provisioned = []
    # A signed event whose client_reference_id matches no account.
    event = {"type": "checkout.session.completed",
             "data": {"object": {"client_reference_id": "ghost"}}}
    pay = PaymentService(Stripe(event), svc, on_paid=lambda a: provisioned.append(a.id))
    res = pay.handle_webhook(b"{}", "good-sig", "whsec")
    assert res == {"handled": False, "reason": "unknown account"}  # no AttributeError / no 400
    assert provisioned == []  # nothing provisioned for a phantom account


# ---------------- M7: server-side input validation on AccountService.create ----------------
@pytest.mark.unit
def test_create_rejects_invalid_email():
    svc = _account_service()
    with pytest.raises(ValueError):
        svc.create("a1", "not-an-email", "+15555550100")


@pytest.mark.unit
def test_create_rejects_disposable_email():
    svc = _account_service()
    with pytest.raises(ValueError):
        svc.create("a1", "burner@mailinator.com", "+15555550100")


@pytest.mark.unit
def test_create_rejects_bad_phone():
    svc = _account_service()
    with pytest.raises(ValueError):
        svc.create("a1", "u@x.com", "not-a-phone")


@pytest.mark.unit
def test_create_normalizes_email_and_phone():
    svc = _account_service()
    acct = svc.create("a1", "  User@Example.COM ", "+1 (555) 555-0100")
    assert acct.email == "user@example.com"  # lowercased + trimmed
    assert acct.phone == "+15555550100"      # E.164-ish: '+' + digits only


@pytest.mark.unit
def test_create_enforces_email_uniqueness():
    svc = _account_service()
    first = svc.create("a1", "dup@x.com", "+15555550100")
    # A *different* account_id but the same email returns the existing account (no duplicate).
    second = svc.create("a2", "DUP@x.com", "+15555550101")
    assert second is first
    assert "a2" not in svc.store.rows  # no second row was inserted


# ---------------- L4: phone-before-email ordering reaches PHONE_VERIFIED ----------------
@pytest.mark.unit
def test_verify_phone_then_email_ends_phone_verified():
    svc = _account_service()
    svc.create("a1", "u@x.com", "+15555550100")
    # Phone first (the previously-stuck ordering), then email.
    svc.verify_phone("a1", True)
    acct = svc.verify_email("a1", True)
    assert acct.email_verified and acct.phone_verified
    assert acct.state is State.PHONE_VERIFIED  # not stuck — fully verified, ready to pay
    assert acct.may_pay


# ---------------- L2: provision() asserts fully_verified (defense in depth) ----------------
@pytest.mark.unit
def test_provision_refuses_unverified_account_even_if_paid():
    svc = _account_service()
    acct = svc.create("a1", "u@x.com", "+15555550100")
    acct.state = State.PAID            # forced into PAID without verifying email/phone
    prov = _provisioner(svc.store)
    with pytest.raises(ValueError):
        prov.provision(acct)


# ---------------- per-tenant Managed Agents ids persisted at provisioning ----------------
class AgentPlane:
    """Agent-plane fake: ensure() returns the created MA ids (the real plane's contract)."""

    def ensure(self, *, tenant_id, workspace_id):
        return {"workspace_id": workspace_id,
                "environment_id": f"env-{tenant_id}",
                "coordinator_id": f"coord-{tenant_id}"}


@pytest.mark.unit
def test_provisioning_upserts_workspace_ids_after_agent_plane_ensure():
    from agents.workspace_store import InMemoryWorkspaceStore

    svc = _account_service()
    acct = _verified_account(svc)
    acct.state = State.PAID
    ws_store = InMemoryWorkspaceStore()
    prov = Provisioner(
        store=svc.store, mint_tenant_id=lambda aid: f"tenant-{aid}", db=DB(),
        anthropic_admin=AnthropicAdmin(), secrets=Secrets(), cognito=Cognito(),
        cube=Recorder(), resend=Recorder(), agent_plane=AgentPlane(),
        workspace_store=ws_store,
    )
    res = prov.provision(acct)
    assert res.ok
    # The row the conversation factory + worker read back (no per-request roster rebuild).
    assert ws_store.get("tenant-a1") == {
        "tenant_id": "tenant-a1",
        "workspace_id": "ws_1",
        "environment_id": "env-tenant-a1",
        "coordinator_id": "coord-tenant-a1",
    }


@pytest.mark.unit
def test_provisioning_skips_workspace_store_when_none():
    # Default workspace_store=None must change nothing (offline tests / DB unconfigured).
    svc = _account_service()
    acct = _verified_account(svc)
    acct.state = State.PAID
    res = _provisioner(svc.store).provision(acct)
    assert res.ok  # no AttributeError; persistence simply skipped


@pytest.mark.unit
def test_prod_deps_noop_agent_plane_returns_stub_ids():
    from api.prod_deps import _Noop

    assert _Noop().ensure(tenant_id="t", workspace_id="ws") == {
        "workspace_id": "stub-ws", "environment_id": "stub-env", "coordinator_id": "stub-coord",
    }


# ---------------- H7: server-side funnel wiring ----------------
class FunnelRecorder:
    """PostHog stand-in: records (distinct_id, event, properties) and group() calls."""
    def __init__(self):
        self.captures = []
        self.groups = []

    def capture(self, distinct_id, event, properties):
        self.captures.append((distinct_id, event, properties))

    def group(self, distinct_id, tenant_id):
        self.groups.append((distinct_id, tenant_id))


@pytest.mark.unit
def test_funnel_records_payment_succeeded_on_webhook():
    svc = _account_service()
    _verified_account(svc)
    rec = FunnelRecorder()
    funnel = Funnel(rec)
    event = {"type": "checkout.session.completed",
             "data": {"object": {"client_reference_id": "a1",
                                 "metadata": {"plan": "pro", "mrr": 99.0}}}}
    pay = PaymentService(Stripe(event), svc, on_paid=lambda a: None, funnel=funnel)
    pay.handle_webhook(b"{}", "good-sig", "whsec")
    events = [e for (_, e, _) in rec.captures]
    assert "payment_succeeded" in events
    distinct_id, _, props = next(c for c in rec.captures if c[1] == "payment_succeeded")
    assert distinct_id == "a1"
    assert props == {"plan": "pro", "mrr": 99.0}


@pytest.mark.unit
def test_funnel_records_instance_provisioned_on_provision():
    svc = _account_service()
    acct = _verified_account(svc)
    acct.state = State.PAID
    rec = FunnelRecorder()
    funnel = Funnel(rec)
    prov = Provisioner(
        store=svc.store, mint_tenant_id=lambda aid: f"tenant-{aid}", db=DB(),
        anthropic_admin=AnthropicAdmin(), secrets=Secrets(), cognito=Cognito(),
        cube=Recorder(), resend=Recorder(), agent_plane=Recorder(), funnel=funnel,
    )
    res = prov.provision(acct)
    assert res.ok
    events = [e for (_, e, _) in rec.captures]
    assert "instance_provisioned" in events
    # grouped under the tenant minted at provisioning
    assert rec.groups == [("a1", "tenant-a1")]
