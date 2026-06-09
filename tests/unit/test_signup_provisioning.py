"""Unit: signup + payment-gated, idempotent, rollback-safe provisioning."""
import pytest

from signup.accounts import AccountService, State
from signup.payment import PaymentError, PaymentService
from signup.provisioning import Provisioner


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
