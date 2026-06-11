"""Unit/integration: the read-only Stripe DATA connector — recorded fixtures
only (tests/fixtures/connectors/stripe_*.json), NO live Stripe call ever.

The credential-isolation contract is the headline here: the connector resolves
the TENANT'S OWN key from uplift/{tenant}/stripe and NOTHING else — by
construction it can never touch the platform's signup/billing Stripe key
(different secret name, different plane). Plus:
  * customers -> contacts, subscriptions -> deals (status as stage, minor
    units -> major), invoices -> activities (revenue summary bodies)
  * expanded customer objects + null names tolerated
  * the incremental cursor is the max `created` epoch (zero-padded for the
    pipeline's lexicographic compare) and the second sync pulls nothing
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingest import EMBEDDING_DIM
from ingest.connectors.base import MissingTenantCredentialError, SecretNotFoundError
from ingest.connectors.stripe_data import StripeDataConnector
from ingest.pipeline import (
    InMemoryCursorStore,
    InMemoryDocumentStore,
    InMemoryRawSink,
    InMemoryStructuredSink,
    sync_tenant,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "connectors"
TENANT = "33333333-3333-3333-3333-333333333333"
TENANT_KEY = "rk_test_tenant_own_restricted_key"


def _fixture(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())["data"]


class RecordedStripeClient:
    """Replays the recorded list fixtures; honors `since` (created[gt]) the way
    the real client does server-side."""

    def __init__(self):
        self.key = None

    def set_key(self, key):
        self.key = key

    @staticmethod
    def _newer(obj, since):
        return not since or int(obj.get("created") or 0) > int(since)

    def list_customers(self, since):
        return [c for c in _fixture("stripe_customers.json") if self._newer(c, since)]

    def list_subscriptions(self, since):
        return [s for s in _fixture("stripe_subscriptions.json") if self._newer(s, since)]

    def list_invoices(self, since):
        return [i for i in _fixture("stripe_invoices.json") if self._newer(i, since)]


class VaultedSecrets:
    def __init__(self, values: dict[str, str]):
        self.values = dict(values)
        self.asked: list[str] = []

    def get_secret(self, ref: str) -> str:
        self.asked.append(ref)
        if ref not in self.values:
            raise SecretNotFoundError(ref)
        return self.values[ref]


def _connector(secrets, client=None):
    return StripeDataConnector(
        TENANT,
        client=client if client is not None else RecordedStripeClient(),
        secrets=secrets,
        raw_sink=InMemoryRawSink(),
        structured_sink=InMemoryStructuredSink(),
    )


def _embed(text: str) -> list[float]:
    return [0.75] * EMBEDDING_DIM


# --------------------------------------------------------------------------- auth
@pytest.mark.unit
def test_only_the_tenants_own_vault_slot_is_consulted():
    # The credential-isolation contract: uplift/{tenant}/stripe and NOTHING
    # else — the platform signup key's secret name is structurally unreachable.
    secrets = VaultedSecrets({f"uplift/{TENANT}/stripe": TENANT_KEY})
    client = RecordedStripeClient()
    conn = _connector(secrets, client)
    conn.authenticate()
    assert secrets.asked == [f"uplift/{TENANT}/stripe"]
    assert client.key == TENANT_KEY


@pytest.mark.unit
def test_missing_or_empty_tenant_key_is_hard_error_no_fallback():
    with pytest.raises(MissingTenantCredentialError):
        _connector(VaultedSecrets({})).authenticate()
    with pytest.raises(MissingTenantCredentialError):
        _connector(VaultedSecrets({f"uplift/{TENANT}/stripe": ""})).authenticate()


@pytest.mark.unit
def test_pull_requires_authenticate_first():
    conn = _connector(VaultedSecrets({f"uplift/{TENANT}/stripe": TENANT_KEY}))
    with pytest.raises(RuntimeError, match="authenticate"):
        list(conn.pull(None))


# --------------------------------------------------------------------------- normalization
@pytest.mark.integration
def test_recorded_fixtures_normalize_to_crm_shapes():
    conn = _connector(VaultedSecrets({f"uplift/{TENANT}/stripe": TENANT_KEY}))
    conn.authenticate()
    by_ref = {r.ref_id: r for r in conn.pull(None)}
    assert len(by_ref) == 6  # 2 customers + 2 subscriptions + 2 invoices

    acme = by_ref["cus_AAA111"]
    assert acme.table == "contacts"
    assert acme.row["name"] == "Acme Fencing LLC"
    assert acme.row["email"] == "billing@acme.test"
    assert acme.row["source"] == "stripe"
    assert acme.tenant_id == TENANT

    # null name falls back to description
    assert by_ref["cus_BBB222"].row["name"] == "Rivertown Pools"

    team = by_ref["sub_X001"]
    assert team.table == "deals"
    assert team.row["title"] == "Team plan"
    assert team.row["amount"] == 199.0          # 19900 minor units -> major
    assert team.row["currency"] == "USD"
    assert team.row["stage"] == "active"
    assert team.row["contact_ref_id"] == "cus_AAA111"
    assert "per month" in team.text_blocks[0]["text"]

    starter = by_ref["sub_X002"]
    assert starter.row["stage"] == "canceled"   # status=all keeps canceled subs
    assert starter.row["title"] == "prod_starter"  # nickname null -> product id

    inv = by_ref["in_Z001"]
    assert inv.table == "activities"
    assert inv.row["kind"] == "invoice"
    assert inv.row["contact_ref_id"] == "cus_AAA111"
    assert inv.row["deal_ref_id"] == "sub_X001"
    assert "199.0 USD" in inv.row["body"]

    # expanded customer object + open status use amount_due
    inv2 = by_ref["in_Z002"]
    assert inv2.row["contact_ref_id"] == "cus_BBB222"
    assert "status open" in inv2.row["body"]
    assert "49.0 USD" in inv2.row["body"]


# --------------------------------------------------------------------------- incremental
@pytest.mark.integration
def test_sync_advances_epoch_cursor_and_second_run_pulls_nothing():
    secrets = VaultedSecrets({f"uplift/{TENANT}/stripe": TENANT_KEY})
    store, cursors = InMemoryDocumentStore(), InMemoryCursorStore()

    first = sync_tenant(TENANT, _connector(secrets), _embed, store, cursors)
    assert first.pulled == 6
    assert first.embedded == 6
    # max created across the fixtures, zero-padded for lexicographic compare
    assert cursors.get(TENANT, "stripe") == "1749340800"

    second = sync_tenant(TENANT, _connector(secrets), _embed, store, cursors)
    assert second.pulled == 0
    assert len(store.docs) == 6  # no duplicates
