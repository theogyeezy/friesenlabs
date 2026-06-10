"""Unit: the server-side PostHog funnel (TODO INT/P3) — client shapes, never-raises, gating.

Proves the four contracts the funnel hangs on, with a transport-mocked client (no network):
  * PAYLOAD SHAPES — capture() POSTs the PostHog /capture/ shape (api_key, event, distinct_id,
    properties, timestamp); a truthy tenant_id property is lifted into `$groups` and group()
    emits `$groupidentify` — the funnel events roll up per tenant;
  * NEVER RAISES — a raising transport (network/serialization failure) is swallowed and logged;
    analytics can never fail a payment webhook or a provisioning step (and park_failed survives
    even a hostile injected funnel);
  * THE GATE — api/prod_deps builds the funnel ONLY under the SIGNUP_REAL_DEPS master switch AND
    the NEW POSTHOG_PROJECT_KEY_VALUE env (deploy invariance: a key present without the switch
    selects nothing); payment + provisioner share ONE client; the Lambda cold-start path
    (build_provisioner bare) selects identically;
  * THE EVENTS — payment_succeeded (signed webhook), instance_provisioned (activate) and
    provisioning_failed (park_failed) all land, grouped under the tenant once minted.
"""
import pytest

import api.prod_deps as prod_deps
from shared.config import Config
from signup.accounts import AccountService, State
from signup.funnel import FUNNEL, Funnel
from signup.posthog_client import TENANT_GROUP_TYPE, PostHogClient
from signup.provisioning import Provisioner

# Reuse the provisioning fakes (the lambda/sfn + integration suites do the same).
from tests.unit.test_signup_provisioning import (
    AnthropicAdmin, Cognito, DB, Email, Recorder, Secrets, Store, Stripe,
)
from signup.payment import PaymentService


class Transport:
    """Injected transport: records (url, payload) synchronously (no thread, no network)."""

    def __init__(self):
        self.sent = []

    def __call__(self, url, payload):
        self.sent.append((url, payload))


def _client(host="https://us.i.posthog.com"):
    t = Transport()
    return PostHogClient("phc_test_key", host, transport=t), t


def _paid_account(aid="a1"):
    store = Store()
    svc = AccountService(store, Cognito(), Email(), Recorder())
    svc.create(aid, "u@x.com", "+15555550100")
    svc.verify_email(aid, True)
    svc.verify_phone(aid, True)
    acct = store.get(aid)
    acct.state = State.PAID
    return store, acct


def _provisioner(store, funnel, admin=None):
    return Provisioner(
        store=store, mint_tenant_id=lambda aid: f"tenant-{aid}", db=DB(),
        anthropic_admin=admin or AnthropicAdmin(), secrets=Secrets(), cognito=Cognito(),
        cube=Recorder(), resend=Recorder(), agent_plane=Recorder(), funnel=funnel,
    )


def _cfg(monkeypatch, dsn=None, **overrides):
    monkeypatch.setattr(prod_deps, "load", lambda: Config(**overrides))
    monkeypatch.setattr(prod_deps, "dsn_from_env", lambda: dsn)


# ---------------------------------------------------------------- payload shapes
@pytest.mark.unit
def test_capture_posts_the_posthog_capture_shape():
    client, t = _client()
    client.capture("acct-1", "payment_succeeded", {"plan": "pro", "mrr": 99.0})
    [(url, payload)] = t.sent
    assert url == "https://us.i.posthog.com/capture/"
    assert payload["api_key"] == "phc_test_key"
    assert payload["event"] == "payment_succeeded"
    assert payload["distinct_id"] == "acct-1"
    assert payload["properties"]["plan"] == "pro" and payload["properties"]["mrr"] == 99.0
    assert "timestamp" in payload   # ISO-8601 UTC — PostHog orders the funnel by it


@pytest.mark.unit
def test_host_is_normalized_with_trailing_slash():
    client, t = _client(host="https://eu.i.posthog.com/")
    client.capture("a", "first_login")
    assert t.sent[0][0] == "https://eu.i.posthog.com/capture/"


@pytest.mark.unit
def test_tenant_id_property_is_lifted_into_groups():
    client, t = _client()
    client.capture("acct-1", "instance_provisioned", {"tenant_id": "tenant-a1"})
    props = t.sent[0][1]["properties"]
    assert props["$groups"] == {TENANT_GROUP_TYPE: "tenant-a1"}
    assert props["tenant_id"] == "tenant-a1"   # the raw property survives too


@pytest.mark.unit
def test_no_tenant_no_groups():
    # payment_succeeded fires from the webhook BEFORE a tenant is minted — no group yet
    # (Funnel.group_tenant attaches the tenant later, at activate).
    client, t = _client()
    client.capture("acct-1", "payment_succeeded", {"plan": "pro"})
    assert "$groups" not in t.sent[0][1]["properties"]
    client.capture("acct-1", "provisioning_failed", {"tenant_id": None, "error": "x"})
    assert "$groups" not in t.sent[1][1]["properties"]   # None tenant never groups


@pytest.mark.unit
def test_group_emits_groupidentify():
    client, t = _client()
    client.group("acct-1", "tenant-a1")
    [(_, payload)] = t.sent
    assert payload["event"] == "$groupidentify"
    assert payload["distinct_id"] == "acct-1"
    props = payload["properties"]
    assert props["$group_type"] == TENANT_GROUP_TYPE
    assert props["$group_key"] == "tenant-a1"
    assert props["$groups"] == {TENANT_GROUP_TYPE: "tenant-a1"}


# ---------------------------------------------------------------- never raises
@pytest.mark.unit
def test_capture_swallows_a_raising_transport():
    def boom(url, payload):
        raise ConnectionError("posthog is down")

    client = PostHogClient("phc_x", transport=boom)
    client.capture("a", "payment_succeeded", {"plan": "pro"})   # must not raise
    client.group("a", "tenant-1")                               # must not raise


@pytest.mark.unit
def test_capture_swallows_unserializable_properties():
    import json

    def strict(url, payload):
        json.dumps(payload)   # a serializing sender raises TypeError — must be swallowed

    client = PostHogClient("phc_x", transport=strict)
    client.capture("a", "payment_succeeded", {"bad": object()})   # must not raise


@pytest.mark.unit
def test_default_transport_posts_json_to_capture(monkeypatch):
    """The urllib path (no injected transport): JSON POST to {host}/capture/ with a timeout."""
    import json
    sent = {}

    class Resp:
        def read(self):
            return b'{"status": 1}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        sent["url"] = req.full_url
        sent["body"] = json.loads(req.data)
        sent["timeout"] = timeout
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = PostHogClient("phc_x", "https://us.i.posthog.com")
    client._post({"api_key": "phc_x", "event": "first_login", "distinct_id": "a",
                  "properties": {}, "timestamp": "t"})
    assert sent["url"] == "https://us.i.posthog.com/capture/"
    assert sent["body"]["event"] == "first_login"
    assert sent["timeout"] == 3.0   # bounded — a slow PostHog can never wedge the sender thread


@pytest.mark.unit
def test_default_transport_swallows_network_failures(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("no route to posthog")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    PostHogClient("phc_x")._post({"api_key": "phc_x"})   # must not raise


@pytest.mark.unit
def test_park_failed_survives_a_raising_funnel():
    """park_failed must NEVER raise (SFN catch-all final state) — even a hostile funnel duck."""
    class HostileFunnel:
        def capture(self, *a, **k):
            raise RuntimeError("analytics exploded")

    store, acct = _paid_account()
    prov = _provisioner(store, HostileFunnel())
    out = prov.park_failed(acct, error="boom")          # must not raise
    assert out["status"] == "ok"
    assert store.get("a1").state is State.PROVISIONING_FAILED


# ---------------------------------------------------------------- the funnel events
@pytest.mark.unit
def test_provisioning_failed_is_a_funnel_event():
    assert "provisioning_failed" in FUNNEL
    # Funnel.capture accepts it (would ValueError on an unknown event).
    client, t = _client()
    Funnel(client).capture("a1", "provisioning_failed", tenant_id="tenant-a1", error="x")
    assert t.sent[0][1]["event"] == "provisioning_failed"


@pytest.mark.unit
def test_park_failed_emits_provisioning_failed_grouped_under_tenant():
    store, acct = _paid_account()
    client, t = _client()
    admin = AnthropicAdmin(fail_on_key=True)   # step 2 fails AFTER the tenant is minted
    prov = _provisioner(store, Funnel(client), admin=admin)
    res = prov.provision(acct)
    assert res.ok is False
    failed = [p for (_, p) in t.sent if p["event"] == "provisioning_failed"]
    assert len(failed) == 1
    props = failed[0]["properties"]
    assert props["tenant_id"] == "tenant-a1"
    assert props["$groups"] == {TENANT_GROUP_TYPE: "tenant-a1"}
    assert "Admin API key creation failed" in props["error"]


@pytest.mark.unit
def test_webhook_to_activate_emits_the_full_server_side_funnel():
    """The INT/P3 done-when, offline: a payment produces payment_succeeded +
    instance_provisioned grouped under the tenant — all server-side, transport-mocked."""
    store, _ = _paid_account()
    acct = store.get("a1")
    acct.state = State.PHONE_VERIFIED          # back to the pre-webhook state
    client, t = _client()
    funnel = Funnel(client)
    prov = _provisioner(store, funnel)
    event = {"id": "evt_1", "type": "checkout.session.completed",
             "data": {"object": {"client_reference_id": "a1",
                                 "metadata": {"plan": "pro", "mrr": 99.0}}}}
    svc = AccountService(store, Cognito(), Email(), Recorder())
    pay = PaymentService(Stripe(event), svc, on_paid=prov.provision, funnel=funnel)
    pay.handle_webhook(b"{}", "good-sig", "whsec")

    events = [p["event"] for (_, p) in t.sent]
    assert events.count("payment_succeeded") == 1
    assert events.count("instance_provisioned") == 1
    assert events.count("$groupidentify") == 1
    provisioned = next(p for (_, p) in t.sent if p["event"] == "instance_provisioned")
    assert provisioned["properties"]["$groups"] == {TENANT_GROUP_TYPE: "tenant-a1"}
    assert store.get("a1").state is State.ACTIVE


# ---------------------------------------------------------------- the prod_deps gate
@pytest.mark.unit
def test_key_without_master_switch_selects_no_funnel(monkeypatch):
    """Deploy invariance: POSTHOG_PROJECT_KEY_VALUE present, switch ABSENT -> nothing real."""
    _cfg(monkeypatch, posthog_project_key_value="phc_live_x")
    deps = prod_deps.build_signup_deps()
    assert deps.payment.funnel is None
    assert deps.payment.on_paid.__self__.funnel is None


@pytest.mark.unit
def test_switch_without_key_selects_no_funnel(monkeypatch):
    _cfg(monkeypatch, signup_real_deps=True)
    deps = prod_deps.build_signup_deps()
    assert deps.payment.funnel is None
    assert deps.payment.on_paid.__self__.funnel is None


@pytest.mark.unit
def test_switch_plus_key_wires_one_shared_funnel(monkeypatch):
    _cfg(monkeypatch, signup_real_deps=True, posthog_project_key_value="phc_live_x",
         posthog_host="https://eu.i.posthog.com")
    deps = prod_deps.build_signup_deps()
    funnel = deps.payment.funnel
    assert isinstance(funnel, Funnel)
    assert isinstance(funnel.posthog, PostHogClient)
    assert funnel.posthog._key == "phc_live_x"
    assert funnel.posthog._endpoint == "https://eu.i.posthog.com/capture/"
    # ONE client across payment + provisioning (the events stitch into one funnel).
    assert deps.payment.on_paid.__self__.funnel is funnel


@pytest.mark.unit
def test_lambda_cold_start_selects_the_funnel_identically(monkeypatch):
    """build_provisioner bare (the SFN Lambda runtime) honors the same gate."""
    _cfg(monkeypatch, signup_real_deps=True, posthog_project_key_value="phc_live_x")
    prov = prod_deps.build_provisioner()
    assert isinstance(prov.funnel, Funnel)
    assert isinstance(prov.funnel.posthog, PostHogClient)
    _cfg(monkeypatch, posthog_project_key_value="phc_live_x")   # switch off
    assert prod_deps.build_provisioner().funnel is None
