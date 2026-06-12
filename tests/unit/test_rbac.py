"""Unit: RBAC — the `cognito:groups` admin gate over the privileged tenant surfaces.

The security audit found NO route checked a role: any authed tenant user could flip the kill
switch, raise autonomy to L3, open the Stripe portal, change paid module entitlements, trigger
the GDPR export, or fire the account delete. This file pins the fix end-to-end:

  * claims parsing — `cognito:groups` (absent/empty/list/defensive-string) -> TenantClaims.groups;
  * the ONE admin policy (api.auth.is_tenant_admin): "admin" group -> admin; explicit non-admin
    groups -> never admin; NO groups -> admin ONLY under the documented back-compat allowance,
    retired by RBAC_STRICT=1 (read per request);
  * every gated write 403s for a member and passes for an admin / empty-groups user;
  * reads stay OPEN to every tenant user (a paused tenant deserves to see WHY);
  * the GLOBAL kill-switch scope is operator-USER-only (CONTROL_GLOBAL_OPERATOR_USERS — subs
    byte-for-byte, emails case-insensitive; unset = NOBODY, fail closed; the legacy
    tenant-granular env grants nothing);
  * provisioning bootstraps the tenant's FIRST user into the "admin" group, best-effort
    (a missing group / old client logs LOUDLY but never fails the pipeline).
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.account_delete_routes import AccountDeleteDeps, mount_account_delete
from api.account_routes import AccountDeps, mount_account
from api.auth import (
    ADMIN_GROUP,
    ADMIN_REQUIRED_DETAIL,
    ENV_RBAC_STRICT,
    TenantClaims,
    is_tenant_admin,
    make_current_tenant,
)
from api.app import ApiDeps, create_app
from api.billing_routes import BillingDeps, mount_billing
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.control.types import Level
from api.views import SavedViews
from api.modules_routes import ModulesDeps, mount_modules
from api.routes_control import ENV_CONTROL_GLOBAL_OPERATORS, mount_control
from api.settings_routes import SettingsDeps, mount_settings

H = {"Authorization": "Bearer t"}


# --------------------------------------------------------------------------- fakes
class GroupVerifier:
    """Verified-claims fake with a configurable `cognito:groups` claim.

    groups=None  -> the claim is ABSENT from the token (every pre-RBAC user);
    groups=[...] -> the claim is present with exactly those entries.
    """

    def __init__(self, groups=None, *, tenant="A", sub="sub-A", email="a@x.com"):
        self.groups, self.tenant, self.sub, self.email = groups, tenant, sub, email

    def verify(self, token):
        claims = {"sub": self.sub, "custom:tenant_id": self.tenant, "email": self.email}
        if self.groups is not None:
            claims["cognito:groups"] = self.groups
        return claims


class _Req:
    """The minimal Request shape current_tenant reads (headers.get only)."""

    def __init__(self, token="t"):
        self.headers = {"authorization": f"Bearer {token}"}


class FakeKillSwitch:
    def __init__(self):
        self.sets = []
        self._engaged = {}

    def status(self, tenant_id):
        return {"engaged": self._engaged.get(tenant_id, False), "scope": "tenant"}

    def set(self, tenant_id, engaged, scope="tenant"):
        self.sets.append((tenant_id, engaged, scope))
        self._engaged[tenant_id] = engaged


class FakeDial:
    def __init__(self):
        self.levels = {}

    def get(self, tenant_id):
        return self.levels.get(tenant_id, Level.L1)

    def set(self, tenant_id, level):
        self.levels[tenant_id] = level


class FakeStripe:
    def create_billing_portal_session(self, *, customer, return_url):
        return {"id": "bps_test", "url": "https://billing.stripe.com/session/bps_test"}


class FakeAccountStore:
    def get_by_tenant_id(self, tenant_id):
        return SimpleNamespace(id=f"acct-{tenant_id}", stripe_customer_id="cus_42", meta={})


class FakeModulesStore:
    def __init__(self):
        self.rows = {}

    def get_modules(self, tenant_id):
        return self.rows.get(str(tenant_id))

    def set_modules(self, tenant_id, ids):
        self.rows[str(tenant_id)] = list(ids)
        return list(ids)


class FakeSettingsStore:
    def get(self, tenant_id):
        return None

    def upsert(self, tenant_id, *, workspace_name=None, notification_prefs=None):
        return {"workspace_name": workspace_name, "notification_prefs": notification_prefs or {}}


class FakeSavedViews:
    def list_views(self, tenant_id):
        return []

    def list_dashboards(self, tenant_id):
        return []


class FakeDeleter:
    def delete_tenant_data(self, *, tenant_id):
        return {"deleted": {"contacts": 1}, "retained": {}, "failed": {}}


# --------------------------------------------------------------------------- app builders
def _control_client(verifier):
    app = FastAPI()
    deps = SimpleNamespace(killswitch=FakeKillSwitch(), autonomy_dial=FakeDial(),
                           autonomy_config=None, trace_store=None)
    mount_control(app, deps, make_current_tenant(verifier))
    return TestClient(app), deps


def _put_killswitch(v):
    c, _ = _control_client(v)
    return c.put("/control/killswitch", json={"engaged": True}, headers=H)


def _put_autonomy(v):
    c, _ = _control_client(v)
    return c.put("/control/autonomy", json={"level": 3}, headers=H)


def _post_portal(v):
    app = FastAPI()
    mount_billing(app, BillingDeps(stripe=FakeStripe(), accounts_store=FakeAccountStore()),
                  make_current_tenant(v))
    return TestClient(app).post("/billing/portal-session", headers=H)


def _put_modules(v):
    app = FastAPI()
    mount_modules(app, ModulesDeps(store=FakeModulesStore()), make_current_tenant(v))
    return TestClient(app).put("/account/modules", json={"enabled": ["cortex"]}, headers=H)


def _get_export(v):
    app = FastAPI()
    mount_account(app, AccountDeps(saved_views=FakeSavedViews()), make_current_tenant(v))
    return TestClient(app).get("/account/export", headers=H)


def _post_delete(v):
    app = FastAPI()
    mount_account_delete(app, AccountDeleteDeps(deleter=FakeDeleter()), make_current_tenant(v))
    return TestClient(app).post("/account/delete", json={"confirm": "A"}, headers=H)


def _put_settings(v):
    app = FastAPI()
    mount_settings(app, SettingsDeps(store=FakeSettingsStore()), make_current_tenant(v))
    return TestClient(app).put("/account/settings", json={"workspace_name": "Acme"}, headers=H)


def _post_decide(v):
    # POST /approvals/{id}/decide -- THE consequential write (approve moves a draft to
    # execution), flagged by review as missing from the original gate set. Full app build
    # (the route lives in create_app, not a mountable router).
    deps = ApiDeps(
        verifier=v, greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: {"ran": True},
    )
    rec = deps.greenlight.propose(tenant_id="A", action="send_email", agent="nadia",
                                  reasoning="r", value_at_stake=1,
                                  payload={"has_unsubscribe": True})
    c = TestClient(create_app(deps))
    return c.post(f"/approvals/{rec['id']}/decide", json={"decision": "approve"}, headers=H)


# Every admin-gated surface the audit named, drivable with one verifier swap.
GATED_WRITES = [
    pytest.param(_put_killswitch, id="PUT /control/killswitch"),
    pytest.param(_put_autonomy, id="PUT /control/autonomy"),
    pytest.param(_post_portal, id="POST /billing/portal-session"),
    pytest.param(_put_modules, id="PUT /account/modules"),
    pytest.param(_get_export, id="GET /account/export"),
    pytest.param(_post_delete, id="POST /account/delete"),
    pytest.param(_put_settings, id="PUT /account/settings"),
    pytest.param(_post_decide, id="POST /approvals/{id}/decide"),
]


# --------------------------------------------------------------------------- groups parsing
@pytest.mark.unit
def test_groups_absent_parses_to_empty_tuple():
    ct = make_current_tenant(GroupVerifier(groups=None))
    assert ct(_Req()).groups == ()


@pytest.mark.unit
def test_groups_empty_list_parses_to_empty_tuple():
    ct = make_current_tenant(GroupVerifier(groups=[]))
    assert ct(_Req()).groups == ()


@pytest.mark.unit
def test_groups_list_parses_in_order():
    ct = make_current_tenant(GroupVerifier(groups=["admin", "member"]))
    assert ct(_Req()).groups == ("admin", "member")


@pytest.mark.unit
def test_groups_defensive_string_parse():
    # A non-Cognito serializer handing back a comma-joined string is tolerated.
    ct = make_current_tenant(GroupVerifier(groups="admin, member"))
    assert ct(_Req()).groups == ("admin", "member")


@pytest.mark.unit
def test_groups_unrecognizable_shape_parses_to_empty():
    # Garbage shapes mean "no groups", never a 500.
    ct = make_current_tenant(GroupVerifier(groups=42))
    assert ct(_Req()).groups == ()


# --------------------------------------------------------------------------- the ONE policy
def _claims(groups=()):
    return TenantClaims(tenant_id="A", sub="sub-A", email="a@x.com", groups=tuple(groups))


@pytest.mark.unit
def test_policy_admin_group_is_admin(monkeypatch):
    monkeypatch.delenv(ENV_RBAC_STRICT, raising=False)
    assert is_tenant_admin(_claims([ADMIN_GROUP])) is True
    assert is_tenant_admin(_claims(["member", ADMIN_GROUP])) is True


@pytest.mark.unit
def test_policy_explicit_non_admin_groups_never_admin(monkeypatch):
    monkeypatch.delenv(ENV_RBAC_STRICT, raising=False)
    assert is_tenant_admin(_claims(["member"])) is False
    assert is_tenant_admin(_claims(["viewer", "member"])) is False


@pytest.mark.unit
def test_policy_empty_groups_is_admin_backcompat(monkeypatch):
    # THE deliberate back-compat allowance: pre-RBAC users have no groups and must not be
    # locked out of their own workspace. Retired by RBAC_STRICT=1.
    monkeypatch.delenv(ENV_RBAC_STRICT, raising=False)
    assert is_tenant_admin(_claims(())) is True


@pytest.mark.unit
def test_policy_strict_mode_removes_empty_groups_allowance(monkeypatch):
    monkeypatch.setenv(ENV_RBAC_STRICT, "1")
    assert is_tenant_admin(_claims(())) is False
    # Strict mode never touches an explicit admin grant.
    assert is_tenant_admin(_claims([ADMIN_GROUP])) is True


@pytest.mark.unit
def test_policy_strict_flag_read_per_request(monkeypatch):
    # Flipping the env mid-process changes the answer — no restart needed.
    monkeypatch.setenv(ENV_RBAC_STRICT, "1")
    assert is_tenant_admin(_claims(())) is False
    monkeypatch.delenv(ENV_RBAC_STRICT)
    assert is_tenant_admin(_claims(())) is True


# --------------------------------------------------------------------------- gated writes
@pytest.mark.unit
@pytest.mark.parametrize("call", GATED_WRITES)
def test_member_gets_403_on_gated_write(call, monkeypatch):
    monkeypatch.delenv(ENV_RBAC_STRICT, raising=False)
    r = call(GroupVerifier(groups=["member"]))
    assert r.status_code == 403
    assert r.json()["detail"] == ADMIN_REQUIRED_DETAIL  # the honest, fixed copy


@pytest.mark.unit
@pytest.mark.parametrize("call", GATED_WRITES)
def test_admin_group_allowed_on_gated_write(call, monkeypatch):
    monkeypatch.delenv(ENV_RBAC_STRICT, raising=False)
    assert call(GroupVerifier(groups=["admin"])).status_code == 200


@pytest.mark.unit
@pytest.mark.parametrize("call", GATED_WRITES)
def test_empty_groups_allowed_backcompat(call, monkeypatch):
    # Every pre-RBAC token (no cognito:groups claim) keeps working until RBAC_STRICT=1.
    monkeypatch.delenv(ENV_RBAC_STRICT, raising=False)
    assert call(GroupVerifier(groups=None)).status_code == 200


@pytest.mark.unit
@pytest.mark.parametrize("call", GATED_WRITES)
def test_strict_mode_rejects_empty_groups(call, monkeypatch):
    monkeypatch.setenv(ENV_RBAC_STRICT, "1")
    r = call(GroupVerifier(groups=None))
    assert r.status_code == 403
    assert r.json()["detail"] == ADMIN_REQUIRED_DETAIL


@pytest.mark.unit
@pytest.mark.parametrize("call", GATED_WRITES)
def test_strict_mode_still_allows_admin(call, monkeypatch):
    monkeypatch.setenv(ENV_RBAC_STRICT, "1")
    assert call(GroupVerifier(groups=["admin"])).status_code == 200


@pytest.mark.unit
def test_unauthed_is_401_before_403():
    # No token resolves 401 in the inner dependency — the admin gate never masks auth.
    c, _ = _control_client(GroupVerifier(groups=["member"]))
    assert c.put("/control/killswitch", json={"engaged": True}).status_code == 401


# --------------------------------------------------------------------------- reads stay open
@pytest.mark.unit
def test_reads_stay_open_to_members(monkeypatch):
    monkeypatch.delenv(ENV_RBAC_STRICT, raising=False)
    member = GroupVerifier(groups=["member"])

    c, _ = _control_client(member)
    assert c.get("/control/killswitch", headers=H).status_code == 200
    assert c.get("/control/autonomy", headers=H).status_code == 200

    app = FastAPI()
    mount_billing(app, BillingDeps(stripe=FakeStripe(), accounts_store=FakeAccountStore()),
                  make_current_tenant(member))
    assert TestClient(app).get("/billing", headers=H).status_code == 200

    app = FastAPI()
    mount_modules(app, ModulesDeps(store=FakeModulesStore()), make_current_tenant(member))
    assert TestClient(app).get("/account/modules", headers=H).status_code == 200

    app = FastAPI()
    mount_settings(app, SettingsDeps(store=FakeSettingsStore()), make_current_tenant(member))
    assert TestClient(app).get("/account/settings", headers=H).status_code == 200


# --------------------------------------------------------------------------- global killswitch
def _global_put(verifier):
    c, deps = _control_client(verifier)
    r = c.put("/control/killswitch", json={"engaged": True, "scope": "global"}, headers=H)
    return r, deps


@pytest.mark.unit
def test_global_unset_env_403_even_for_admin(monkeypatch):
    monkeypatch.delenv(ENV_CONTROL_GLOBAL_OPERATORS, raising=False)
    monkeypatch.delenv(ENV_RBAC_STRICT, raising=False)
    r, deps = _global_put(GroupVerifier(groups=["admin"]))
    assert r.status_code == 403  # unset/empty = NOBODY (fail closed)
    assert deps.killswitch.sets == []  # nothing flipped


@pytest.mark.unit
def test_global_allowlisted_sub_allowed_others_403(monkeypatch):
    monkeypatch.delenv(ENV_RBAC_STRICT, raising=False)
    monkeypatch.setenv(ENV_CONTROL_GLOBAL_OPERATORS, " sub-A , other-operator ")
    r, deps = _global_put(GroupVerifier(groups=["admin"], sub="sub-A"))
    assert r.status_code == 200
    assert deps.killswitch.sets == [("A", True, "global")]
    # A different sub (same tenant!) is NOT an operator — user-granular, never tenant-granular.
    r, deps = _global_put(GroupVerifier(groups=["admin"], sub="sub-B", email="b@x.com"))
    assert r.status_code == 403
    assert deps.killswitch.sets == []


@pytest.mark.unit
def test_global_email_match_is_case_insensitive(monkeypatch):
    monkeypatch.delenv(ENV_RBAC_STRICT, raising=False)
    monkeypatch.setenv(ENV_CONTROL_GLOBAL_OPERATORS, "OPS@FriesenLabs.com")
    r, _ = _global_put(GroupVerifier(groups=["admin"], sub="sub-Z",
                                     email="ops@friesenlabs.com"))
    assert r.status_code == 200
    # …and the other direction (lowercase entry, uppercase claim).
    monkeypatch.setenv(ENV_CONTROL_GLOBAL_OPERATORS, "ops@friesenlabs.com")
    r, _ = _global_put(GroupVerifier(groups=["admin"], sub="sub-Z",
                                     email="OPS@FRIESENLABS.COM"))
    assert r.status_code == 200


@pytest.mark.unit
def test_global_subs_match_byte_for_byte(monkeypatch):
    # Subs are opaque ids: NO case folding for them (only emails fold).
    monkeypatch.delenv(ENV_RBAC_STRICT, raising=False)
    monkeypatch.setenv(ENV_CONTROL_GLOBAL_OPERATORS, "SUB-A")
    r, _ = _global_put(GroupVerifier(groups=["admin"], sub="sub-A", email=None))
    assert r.status_code == 403


@pytest.mark.unit
def test_legacy_tenant_env_grants_nothing(monkeypatch):
    # The v1 tenant-granular allowlist must never grant global again.
    monkeypatch.delenv(ENV_CONTROL_GLOBAL_OPERATORS, raising=False)
    monkeypatch.delenv(ENV_RBAC_STRICT, raising=False)
    monkeypatch.setenv("CONTROL_GLOBAL_OPERATOR_TENANTS", "A")
    r, deps = _global_put(GroupVerifier(groups=["admin"], tenant="A"))
    assert r.status_code == 403
    assert deps.killswitch.sets == []


# --------------------------------------------------------------------------- cognito group write
class GroupCidp:
    """boto3 cognito-idp stand-in for AdminAddUserToGroup (records calls; idempotent like the
    real API — re-adding an existing member succeeds); `missing_group=True` models the
    ResourceNotFoundException of a group whose terraform has not been applied yet."""

    def __init__(self, *, missing_group=False):
        self.calls = []
        self.members = {}  # group -> set of usernames
        self.missing_group = missing_group

    def admin_add_user_to_group(self, **kw):
        self.calls.append(("admin_add_user_to_group", kw))
        if self.missing_group:
            raise RuntimeError("ResourceNotFoundException: Group not found.")
        self.members.setdefault(kw["GroupName"], set()).add(kw["Username"])


@pytest.mark.unit
def test_add_user_to_group_calls_the_exact_api():
    from signup.cognito_admin import CognitoAdminClient

    fake = GroupCidp()
    client = CognitoAdminClient("us-east-1_TestPool", client=fake)
    client.add_user_to_group("sub-1", "admin")
    name, kw = fake.calls[0]
    assert name == "admin_add_user_to_group"
    assert kw == {"UserPoolId": "us-east-1_TestPool", "Username": "sub-1", "GroupName": "admin"}
    assert fake.members == {"admin": {"sub-1"}}


@pytest.mark.unit
def test_add_user_to_group_is_idempotent():
    from signup.cognito_admin import CognitoAdminClient

    fake = GroupCidp()
    client = CognitoAdminClient("us-east-1_TestPool", client=fake)
    client.add_user_to_group("sub-1", "admin")
    client.add_user_to_group("sub-1", "admin")  # re-add succeeds (the real API's behavior)
    assert fake.members == {"admin": {"sub-1"}}
    assert len(fake.calls) == 2


@pytest.mark.unit
def test_add_user_to_group_raises_when_group_missing():
    # The client RAISES (the caller owns the policy — provisioning is the tolerant one).
    from signup.cognito_admin import CognitoAdminClient

    client = CognitoAdminClient("us-east-1_TestPool", client=GroupCidp(missing_group=True))
    with pytest.raises(RuntimeError):
        client.add_user_to_group("sub-1", "admin")


@pytest.mark.unit
def test_add_user_to_group_unconfigured_raises_clean_stub_error():
    from signup.cognito_admin import CognitoAdminClient, CognitoNotConfiguredError

    with pytest.raises(CognitoNotConfiguredError):
        CognitoAdminClient("").add_user_to_group("sub-1", "admin")


# --------------------------------------------------------------------------- provisioning
class ProvCognito:
    """The test_signup_provisioning Cognito fake + add_user_to_group recording."""

    def __init__(self):
        self.users = {}
        self.tenant_set = {}
        self.confirmed = set()
        self.group_adds = []
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

    def add_user_to_group(self, sub, group_name):
        self.group_adds.append((sub, group_name))


class GroupBoomCognito(ProvCognito):
    def add_user_to_group(self, sub, group_name):
        raise RuntimeError("ResourceNotFoundException: Group not found.")


def _provision_with(cognito):
    from signup.accounts import AccountService, State
    from signup.provisioning import Provisioner
    from tests.unit.test_signup_provisioning import (
        AnthropicAdmin, DB, Email, Recorder, Secrets, Store,
    )

    store = Store()
    svc = AccountService(store, cognito, Email(), Recorder())
    svc.create("a1", "u@x.com", "+15555550100")
    svc.verify_email("a1", True)
    svc.verify_phone("a1", True)
    acct = store.get("a1")
    acct.state = State.PAID
    prov = Provisioner(store=store, mint_tenant_id=lambda aid: f"tenant-{aid}", db=DB(),
                       anthropic_admin=AnthropicAdmin(), secrets=Secrets(), cognito=cognito,
                       cube=Recorder(), resend=Recorder())
    return prov, acct


@pytest.mark.unit
def test_provisioning_adds_first_user_to_admin_group():
    cognito = ProvCognito()
    prov, acct = _provision_with(cognito)
    res = prov.provision(acct)
    assert res.ok
    assert cognito.group_adds == [(acct.cognito_sub, "admin")]
    # …and AFTER the claim + confirm (the user exists and is usable when the grant lands).
    assert cognito.tenant_set[acct.cognito_sub] == "tenant-a1"
    assert acct.cognito_sub in cognito.confirmed


@pytest.mark.unit
def test_provisioning_group_failure_is_loud_but_nonfatal(caplog):
    # The group's terraform may not be applied yet — the pipeline MUST still finish (a charged
    # customer with no instance is strictly worse), with a LOUD warning naming the remediation.
    cognito = GroupBoomCognito()
    prov, acct = _provision_with(cognito)
    with caplog.at_level(logging.WARNING, logger="signup.provisioning"):
        res = prov.provision(acct)
    assert res.ok
    from signup.accounts import State
    assert acct.state is State.ACTIVE
    warnings = [r for r in caplog.records if "admin" in r.getMessage()]
    assert warnings, "expected a loud warning about the failed admin-group grant"
    assert "ResourceNotFoundException" in warnings[0].getMessage()
    assert "Provisioning continues" in warnings[0].getMessage()


@pytest.mark.unit
def test_provisioning_tolerates_cognito_client_without_group_method(caplog):
    # An older injected cognito client (no add_user_to_group at all) degrades the same way.
    from tests.unit.test_signup_provisioning import Cognito

    cognito = Cognito()
    prov, acct = _provision_with(cognito)
    with caplog.at_level(logging.WARNING, logger="signup.provisioning"):
        res = prov.provision(acct)
    assert res.ok
    assert any("add_user_to_group" in r.getMessage() for r in caplog.records)


@pytest.mark.unit
def test_provisioning_rerun_does_not_regrant(caplog):
    # An already-ACTIVE account short-circuits — no duplicate grant on a re-delivered webhook.
    cognito = ProvCognito()
    prov, acct = _provision_with(cognito)
    assert prov.provision(acct).ok
    assert prov.provision(acct).steps_done == ["already_active"]
    assert cognito.group_adds == [(acct.cognito_sub, "admin")]
