"""Unit: CognitoAdminClient — mocked boto3 (NO network): MessageAction=SUPPRESS, the exact
`custom:tenant_id` attribute name (THE TRUST RULE's write side), idempotency on
UsernameExists / already-CONFIRMED, and the AccountService + Provisioner duck-type contract.
"""
import sys
import types

import pytest

from signup.cognito_admin import CognitoAdminClient, CognitoNotConfiguredError, from_config


class UsernameExists(Exception):
    pass


class NotAuthorized(Exception):
    pass


class FakeCidp:
    """boto3 cognito-idp stand-in: in-memory user pool + recorded calls + exception shapes.

    Models the REAL status machine that broke login (revenue lane): admin_create_user lands the
    user in FORCE_CHANGE_PASSWORD (where the Hosted UI password flow refuses to sign them in),
    and only admin_set_user_password(Permanent=True) flips them to CONFIRMED.
    """

    def __init__(self):
        self.calls = []
        self.users = {}  # username(email) -> {"sub", "attrs", "status", "password"}
        self._n = 0
        self.exceptions = types.SimpleNamespace(
            UsernameExistsException=UsernameExists,
            NotAuthorizedException=NotAuthorized,
        )

    def _lookup(self, username):
        if username in self.users:
            return self.users[username]
        for u in self.users.values():  # admin APIs accept the sub as the username value
            if u["sub"] == username:
                return u
        raise KeyError(username)

    def admin_create_user(self, **kw):
        self.calls.append(("admin_create_user", kw))
        username = kw["Username"]
        if username in self.users:
            raise UsernameExists(username)
        self._n += 1
        sub = f"sub-{self._n}"
        # The REAL post-admin_create_user state — NOT 'UNCONFIRMED' (that's self-signup only).
        self.users[username] = {"sub": sub, "attrs": {},
                                "status": "FORCE_CHANGE_PASSWORD", "password": None}
        return {"User": {"Username": username,
                         "Attributes": [{"Name": "sub", "Value": sub},
                                        {"Name": "email", "Value": username}]}}

    def admin_get_user(self, **kw):
        self.calls.append(("admin_get_user", kw))
        u = self._lookup(kw["Username"])
        return {"Username": kw["Username"],
                "UserStatus": u["status"],
                "UserAttributes": [{"Name": "sub", "Value": u["sub"]}]}

    def admin_update_user_attributes(self, **kw):
        self.calls.append(("admin_update_user_attributes", kw))
        u = self._lookup(kw["Username"])
        for attr in kw["UserAttributes"]:
            u["attrs"][attr["Name"]] = attr["Value"]

    def admin_set_user_password(self, **kw):
        self.calls.append(("admin_set_user_password", kw))
        u = self._lookup(kw["Username"])
        u["password"] = kw["Password"]
        if kw.get("Permanent"):
            u["status"] = "CONFIRMED"   # the act that actually CONFIRMs an admin-created user

    def admin_confirm_sign_up(self, **kw):
        self.calls.append(("admin_confirm_sign_up", kw))
        u = self._lookup(kw["Username"])
        if u["status"] == "CONFIRMED":
            raise NotAuthorized("User cannot be confirmed. Current status is CONFIRMED")
        if u["status"] == "FORCE_CHANGE_PASSWORD":
            # The real API refuses admin-created users here — the exact bug this lane fixes.
            raise NotAuthorized(
                "User cannot be confirmed. Current status is FORCE_CHANGE_PASSWORD"
            )
        u["status"] = "CONFIRMED"


def _client(fake=None):
    fake = fake or FakeCidp()
    return CognitoAdminClient("us-east-1_TestPool", client=fake), fake


# ---------------- import safety / lazy boto3 ----------------
@pytest.mark.unit
def test_injected_client_never_touches_boto3(monkeypatch):
    # Poison boto3: any `import boto3` would now raise ImportError. Construction and every call
    # through an INJECTED client must still work (boto3 is imported lazily, only on the real path).
    monkeypatch.setitem(sys.modules, "boto3", None)
    client = CognitoAdminClient("us-east-1_TestPool", client=FakeCidp())
    sub = client.create_unconfirmed_user("u@x.com")
    assert sub.startswith("sub-")
    client.set_tenant_id(sub, "tenant-1")
    client.confirm(sub)


@pytest.mark.unit
def test_unconfigured_pool_raises_clean_stub_error():
    client = CognitoAdminClient("")  # no pool, no injected client
    with pytest.raises(CognitoNotConfiguredError):
        client.create_unconfirmed_user("u@x.com")
    with pytest.raises(CognitoNotConfiguredError):
        client.set_tenant_id("sub-1", "tenant-1")
    with pytest.raises(CognitoNotConfiguredError):
        client.confirm("sub-1")


@pytest.mark.unit
def test_from_config_default_is_unconfigured(monkeypatch):
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    client = from_config()
    with pytest.raises(CognitoNotConfiguredError):
        client.confirm("sub-1")


# ---------------- create: SUPPRESS + unconfirmed + idempotent ----------------
@pytest.mark.unit
def test_create_user_suppresses_cognito_email_and_returns_sub():
    client, fake = _client()
    sub = client.create_unconfirmed_user("u@x.com")
    assert sub == "sub-1"
    name, kw = fake.calls[0]
    assert name == "admin_create_user"
    assert kw["MessageAction"] == "SUPPRESS"          # Cognito never sends its invite email
    assert kw["UserPoolId"] == "us-east-1_TestPool"
    attrs = {a["Name"]: a["Value"] for a in kw["UserAttributes"]}
    assert attrs["email"] == "u@x.com"
    assert attrs["email_verified"] == "false"          # verification is OURS (Resend link)
    assert "custom:tenant_id" not in attrs             # NO tenant at signup — minted at provisioning
    # The REAL post-create state: NOT usable for Hosted UI login until confirm() fixes it.
    assert fake.users["u@x.com"]["status"] == "FORCE_CHANGE_PASSWORD"


@pytest.mark.unit
def test_create_is_idempotent_on_username_exists():
    client, fake = _client()
    first = client.create_unconfirmed_user("u@x.com")
    second = client.create_unconfirmed_user("u@x.com")  # UsernameExistsException tolerated
    assert second == first                              # same sub, no duplicate user
    assert [c[0] for c in fake.calls] == [
        "admin_create_user", "admin_create_user", "admin_get_user",
    ]
    assert len(fake.users) == 1


# ---------------- THE TRUST RULE write side: custom:tenant_id ----------------
@pytest.mark.unit
def test_set_tenant_id_writes_exactly_custom_tenant_id():
    client, fake = _client()
    sub = client.create_unconfirmed_user("u@x.com")
    client.set_tenant_id(sub, "tenant-42")
    name, kw = fake.calls[-1]
    assert name == "admin_update_user_attributes"
    assert kw["UserPoolId"] == "us-east-1_TestPool"
    assert kw["Username"] == sub                       # addressed by the immutable sub
    # The EXACT attribute name the JWT verifier (api/auth.py) trusts downstream.
    assert kw["UserAttributes"] == [{"Name": "custom:tenant_id", "Value": "tenant-42"}]
    assert fake.users["u@x.com"]["attrs"]["custom:tenant_id"] == "tenant-42"


# ---------------- confirm: the FORCE_CHANGE_PASSWORD fix + idempotency ----------------
@pytest.mark.unit
def test_confirm_admin_created_user_sets_permanent_password_and_confirms():
    """The login fix: an admin-created (FORCE_CHANGE_PASSWORD) user is CONFIRMed via
    admin_set_user_password(Permanent=True) — NOT admin_confirm_sign_up (which the real API
    refuses for admin-created users, leaving them unable to log in via the Hosted UI)."""
    client, fake = _client()
    sub = client.create_unconfirmed_user("u@x.com")
    client.confirm(sub)
    user = fake.users["u@x.com"]
    assert user["status"] == "CONFIRMED"               # Hosted UI login now possible
    name, kw = next(c for c in fake.calls if c[0] == "admin_set_user_password")
    assert kw["Permanent"] is True
    assert kw["Username"] == sub
    pw = kw["Password"]
    # The generated single-use credential is strong (every Cognito policy class) and DISCARDED.
    assert len(pw) >= 12
    assert any(c.isupper() for c in pw) and any(c.islower() for c in pw)
    assert any(c.isdigit() for c in pw) and any(not c.isalnum() for c in pw)
    # email_verified flips true (OUR Resend flow verified it) so Hosted UI forgot-password —
    # the user's real onboarding credential path — can deliver its reset code.
    assert user["attrs"]["email_verified"] == "true"
    # The dead-for-admin-created-users API was never called.
    assert all(c[0] != "admin_confirm_sign_up" for c in fake.calls)


@pytest.mark.unit
def test_confirm_redelivery_never_clobbers_a_user_set_password():
    client, fake = _client()
    sub = client.create_unconfirmed_user("u@x.com")
    client.confirm(sub)
    first_pw = fake.users["u@x.com"]["password"]
    # The user has since set their own password via forgot-password.
    fake.users["u@x.com"]["password"] = "user-chose-this"
    client.confirm(sub)   # re-delivered webhook / SFN retry — must be a pure no-op
    assert fake.users["u@x.com"]["password"] == "user-chose-this"
    assert fake.users["u@x.com"]["status"] == "CONFIRMED"
    assert [c[0] for c in fake.calls].count("admin_set_user_password") == 1
    assert first_pw != "user-chose-this"


@pytest.mark.unit
def test_confirm_unconfirmed_self_signup_keeps_legacy_path():
    # A self-signed-up (UNCONFIRMED) user still goes through admin_confirm_sign_up.
    client, fake = _client()
    sub = client.create_unconfirmed_user("u@x.com")
    fake.users["u@x.com"]["status"] = "UNCONFIRMED"   # simulate a self-signup pool
    client.confirm(sub)
    assert fake.users["u@x.com"]["status"] == "CONFIRMED"
    assert [c[0] for c in fake.calls].count("admin_confirm_sign_up") == 1
    assert all(c[0] != "admin_set_user_password" for c in fake.calls)


@pytest.mark.unit
def test_confirm_reraises_not_authorized_that_is_not_already_confirmed():
    # NotAuthorizedException also covers REAL failures (missing IAM perms, disabled user) —
    # only the already-CONFIRMED replay may be swallowed; anything else must surface so the
    # provisioning step fails (and parks/rolls back) instead of silently passing.
    fake = FakeCidp()
    fake.users["u@x.com"] = {"sub": "sub-1", "attrs": {}, "status": "UNCONFIRMED",
                             "password": None}

    def deny(**kw):
        raise NotAuthorized("Access denied: not authorized to perform AdminConfirmSignUp")

    fake.admin_confirm_sign_up = deny
    client = CognitoAdminClient("us-east-1_TestPool", client=fake)
    with pytest.raises(NotAuthorized):
        client.confirm("sub-1")


# ---------------- the exact AccountService + Provisioner duck-type contract ----------------
@pytest.mark.unit
def test_provisioning_pipeline_runs_through_the_real_client():
    from signup.accounts import AccountService, State
    from signup.provisioning import Provisioner
    from tests.unit.test_signup_provisioning import (
        AnthropicAdmin, DB, Email, Recorder, Secrets, Store,
    )

    fake = FakeCidp()
    cognito = CognitoAdminClient("us-east-1_TestPool", client=fake)
    store = Store()
    svc = AccountService(store, cognito, Email(), Recorder())
    svc.create("a1", "u@x.com", "+15555550100")
    svc.verify_email("a1", True)
    svc.verify_phone("a1", True)
    acct = store.get("a1")
    acct.state = State.PAID

    prov = Provisioner(store=store, mint_tenant_id=lambda aid: f"tenant-{aid}", db=DB(),
                       anthropic_admin=AnthropicAdmin(), secrets=Secrets(), cognito=cognito,
                       cube=Recorder(), resend=Recorder(), agent_plane=Recorder())
    res = prov.provision(acct)
    assert res.ok
    user = fake.users["u@x.com"]
    assert user["attrs"]["custom:tenant_id"] == "tenant-a1"  # claim written at provisioning
    assert user["status"] == "CONFIRMED"   # confirmed (permanent-password path) AFTER the claim
