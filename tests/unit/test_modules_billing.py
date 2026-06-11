"""Unit: Phase-2 module billing ("selection sets the price").

Covers three layers, all offline (no network, no `stripe` lib installed):
  * shared.modules price helpers (env -> Price id resolution; inert when unset).
  * StripeAdapter.sync_subscription_modules (the subscription-item reconcile, fake stripe lib).
  * api.module_billing.ModuleBillingSync + from_env (tenant -> customer -> sync orchestration).
"""
import types

import pytest

from shared import modules as M
from signup.stripe_adapter import StripeAdapter, StripeNotConfiguredError
from api.module_billing import ModuleBillingSync, from_env


# ----------------------------------------------------------------- catalog helpers
@pytest.mark.unit
def test_configured_module_prices_only_returns_set_env():
    env = {"STRIPE_PRICE_ID_MODULE_CORTEX": "price_cortex", "STRIPE_PRICE_ID_MODULE_UPLIFT": "  "}
    prices = M.configured_module_prices(env)
    assert prices == {"cortex": "price_cortex"}  # blank/whitespace is treated as unset


@pytest.mark.unit
def test_configured_module_prices_empty_when_nothing_set():
    assert M.configured_module_prices({}) == {}


@pytest.mark.unit
def test_desired_module_prices_intersects_enabled_and_configured():
    env = {
        "STRIPE_PRICE_ID_MODULE_CORTEX": "price_cortex",
        "STRIPE_PRICE_ID_MODULE_UPLIFT": "price_uplift",
        "STRIPE_PRICE_ID_MODULE_COMMAND": "price_command",
    }
    # enabled = cortex (+ command forced on). uplift is configured but NOT enabled -> excluded.
    desired = M.desired_module_prices(["cortex"], env)
    assert desired == {"cortex": "price_cortex", "command": "price_command"}


@pytest.mark.unit
def test_price_env_names_cover_every_module():
    names = M.price_env_names()
    assert "STRIPE_PRICE_ID_MODULE_CORTEX" in names
    assert len(names) == len([m for m in M.MODULES if m.price_env])


# ----------------------------------------------------------------- adapter sync
def _fake_stripe(calls, *, sub):
    """A duck-typed `stripe` module exposing only what sync_subscription_modules uses."""
    def sub_list(**kw):
        calls.append(("Subscription.list", kw))
        return [sub] if sub is not None else []

    def item_create(**kw):
        calls.append(("SubscriptionItem.create", kw))
        return {"id": "si_new"}

    def item_delete(iid, **kw):
        calls.append(("SubscriptionItem.delete", {"id": iid, **kw}))
        return {"id": iid, "deleted": True}

    return types.SimpleNamespace(
        Subscription=types.SimpleNamespace(list=sub_list),
        SubscriptionItem=types.SimpleNamespace(create=item_create, delete=item_delete),
    )


def _sub(price_to_item):
    """A fake subscription StripeObject-ish dict with item data."""
    return {
        "id": "sub_1",
        "items": {"data": [{"id": iid, "price": {"id": pid}} for pid, iid in price_to_item.items()]},
    }


def _adapter(calls, *, sub, api_key="sk_test_x"):
    return StripeAdapter(api_key, {}, stripe_module=_fake_stripe(calls, sub=sub))


@pytest.mark.unit
def test_sync_requires_api_key():
    with pytest.raises(StripeNotConfiguredError):
        StripeAdapter("", {}).sync_subscription_modules(
            customer="cus_1", desired_price_ids=["p1"], managed_price_ids=["p1"])


@pytest.mark.unit
def test_sync_adds_missing_and_removes_unwanted_managed_items():
    calls = []
    # Subscription currently has the plan-tier item + the cortex module item.
    sub = _sub({"price_plan": "si_plan", "price_cortex": "si_cortex"})
    adapter = _adapter(calls, sub=sub)
    # Desired = uplift only (cortex should be removed, uplift added). plan item is UNMANAGED.
    res = adapter.sync_subscription_modules(
        customer="cus_1",
        desired_price_ids=["price_uplift"],
        managed_price_ids=["price_cortex", "price_uplift"],
    )
    assert res["subscription"] == "sub_1"
    assert res["added"] == ["price_uplift"]
    assert res["removed"] == ["price_cortex"]
    # The plan-tier (unmanaged) item is never deleted.
    deletes = [c for c in calls if c[0] == "SubscriptionItem.delete"]
    assert all(c[1]["id"] != "si_plan" for c in deletes)
    assert {c[1]["id"] for c in deletes} == {"si_cortex"}


@pytest.mark.unit
def test_sync_is_noop_when_already_in_sync():
    calls = []
    sub = _sub({"price_plan": "si_plan", "price_cortex": "si_cortex"})
    adapter = _adapter(calls, sub=sub)
    res = adapter.sync_subscription_modules(
        customer="cus_1", desired_price_ids=["price_cortex"], managed_price_ids=["price_cortex"])
    assert res["added"] == [] and res["removed"] == []
    assert not [c for c in calls if c[0] in ("SubscriptionItem.create", "SubscriptionItem.delete")]


@pytest.mark.unit
def test_sync_no_active_subscription_is_clean_noop():
    calls = []
    adapter = _adapter(calls, sub=None)
    res = adapter.sync_subscription_modules(
        customer="cus_1", desired_price_ids=["price_cortex"], managed_price_ids=["price_cortex"])
    assert res == {"subscription": None, "added": [], "removed": []}


# ----------------------------------------------------------------- orchestration
class _Acct:
    def __init__(self, customer):
        self.stripe_customer_id = customer


class _Accounts:
    def __init__(self, by_tenant):
        self._by = by_tenant

    def get_by_tenant_id(self, tenant_id):
        return self._by.get(str(tenant_id))


class _RecordingStripe:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def sync_subscription_modules(self, **kw):
        self.calls.append(kw)
        return self.result


ENV = {"STRIPE_PRICE_ID_MODULE_CORTEX": "price_cortex", "STRIPE_PRICE_ID_MODULE_COMMAND": "price_command"}


@pytest.mark.unit
def test_from_env_inert_without_configured_prices():
    # No per-module Prices -> None (billing skipped; Phase-1 behavior).
    assert from_env(accounts_store=_Accounts({}), stripe=_RecordingStripe({}), env={}) is None


@pytest.mark.unit
def test_from_env_inert_without_stripe_or_accounts():
    assert from_env(accounts_store=None, stripe=_RecordingStripe({}), env=ENV) is None
    assert from_env(accounts_store=_Accounts({}), stripe=None, env=ENV) is None


@pytest.mark.unit
def test_from_env_builds_when_configured():
    assert from_env(accounts_store=_Accounts({}), stripe=_RecordingStripe({}), env=ENV) is not None


@pytest.mark.unit
def test_sync_no_customer_is_noop_status():
    sync = ModuleBillingSync(accounts_store=_Accounts({}), stripe=_RecordingStripe({}), env=ENV)
    assert sync.sync("T", ["cortex"]) == {"status": "no_customer"}


@pytest.mark.unit
def test_sync_no_subscription_status():
    stripe = _RecordingStripe({"subscription": None, "added": [], "removed": []})
    sync = ModuleBillingSync(accounts_store=_Accounts({"T": _Acct("cus_1")}), stripe=stripe, env=ENV)
    assert sync.sync("T", ["cortex"]) == {"status": "no_subscription"}


@pytest.mark.unit
def test_sync_passes_desired_and_managed_and_returns_synced():
    stripe = _RecordingStripe({"subscription": "sub_1", "added": ["price_cortex"], "removed": []})
    sync = ModuleBillingSync(accounts_store=_Accounts({"T": _Acct("cus_1")}), stripe=stripe, env=ENV)
    out = sync.sync("T", ["cortex"])
    assert out == {"status": "synced", "added": ["price_cortex"], "removed": []}
    kw = stripe.calls[-1]
    assert kw["customer"] == "cus_1"
    # desired = enabled∩configured = cortex + command (forced on); managed = all configured prices.
    assert set(kw["desired_price_ids"]) == {"price_cortex", "price_command"}
    assert set(kw["managed_price_ids"]) == {"price_cortex", "price_command"}
