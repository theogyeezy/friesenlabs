"""Unit: POST /account/delete — offboarding / GDPR teardown (api/account_delete_routes.py).

Mounts ``mount_account_delete`` on a bare FastAPI app with a fake verifier and a fake in-memory
deleter; zero DB, zero AWS. Covers:

  * 503 unconfigured: deleter None -> 503, never 500 (the export-sibling inert-deps contract)
  * 401 unauth: missing / invalid bearer -> 401
  * 422 confirm guard: absent body, missing/blank confirm, or a confirm naming the WRONG tenant
  * successful teardown: per-table deleted counts for the mutable tables
  * append-only retained: audit tables reported under `retained` with a reason, never deleted
  * idempotent re-run: a second teardown reports zeros (and re-confirms retained)
  * cross-tenant safety: only the CLAIM tenant's data is targeted; the other tenant is untouched,
    and a confirm token that names another tenant is refused (can't widen the blast radius)
  * tenant from claim: the report echoes the verified-claim tenant, not a body/query value
  * per-table failure isolation: a table that errors is reported under `failed`, the rest still delete
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.account_delete_routes import AccountDeleteDeps, mount_account_delete
from api.auth import make_current_tenant
from api.pg_account_delete import DELETABLE_TABLES, RETAINED_TABLES


# --------------------------------------------------------------------------- fakes

class FakeVerifier:
    """Accepts any non-empty Bearer; maps 't-A' -> tenant 'A', 't-B' -> tenant 'B'."""

    def verify(self, token: str) -> dict:
        tenant = token.split("-")[1] if token.startswith("t-") else "A"
        return {"sub": f"sub-{tenant}", "custom:tenant_id": tenant, "email": f"{tenant}@x.com"}


class FakeDeleter:
    """In-memory deleter that mirrors PgAccountDeleter's contract WITHOUT a DB.

    Holds per-tenant row counts for the mutable (deletable) tables. `delete_tenant_data` zeroes
    ONLY the given tenant's mutable tables (RLS-equivalent), reports the counts it removed, reports
    every append-only table as retained, and never touches another tenant's data. `fail_tables`
    simulates a per-table DELETE failure (the savepoint-isolation path).
    """

    def __init__(self, rows: dict[str, dict[str, int]] | None = None,
                 fail_tables: set[str] | None = None):
        # rows: {tenant_id -> {table -> count}}; missing tables default to 0.
        self._rows: dict[str, dict[str, int]] = rows or {}
        self._fail = fail_tables or set()
        self.calls: list[str] = []  # tenant_ids this deleter was asked to tear down

    def delete_tenant_data(self, *, tenant_id: str) -> dict:
        self.calls.append(str(tenant_id))
        tenant_rows = self._rows.setdefault(str(tenant_id), {})
        deleted: dict[str, int] = {}
        failed: dict[str, str] = {}
        for table in DELETABLE_TABLES:
            if table in self._fail:
                failed[table] = "FakeError"
                continue
            count = int(tenant_rows.get(table, 0))
            deleted[table] = count
            tenant_rows[table] = 0  # idempotent: a re-run finds nothing
        return {"deleted": deleted, "retained": dict(RETAINED_TABLES), "failed": failed}


# --------------------------------------------------------------------------- helpers

H_A = {"Authorization": "Bearer t-A"}
H_B = {"Authorization": "Bearer t-B"}


def _client(deleter=None, secret_writer=None) -> TestClient:
    app = FastAPI()
    deps = AccountDeleteDeps(deleter=deleter, secret_writer=secret_writer)
    mount_account_delete(app, deps, make_current_tenant(FakeVerifier()))
    return TestClient(app)


def _full_deleter() -> FakeDeleter:
    """A deleter holding one row in every mutable table for tenants A and B."""
    return FakeDeleter(rows={
        "A": {t: 1 for t in DELETABLE_TABLES},
        "B": {t: 7 for t in DELETABLE_TABLES},
    })


# --------------------------------------------------------------------------- 503 unconfigured

@pytest.mark.unit
def test_delete_503_when_deleter_none():
    """No deleter wired -> 503 (honest 'nothing to delete'), never 500."""
    c = _client(deleter=None)
    r = c.post("/account/delete", headers=H_A, json={"confirm": "A"})
    assert r.status_code == 503
    assert "configured" in r.json()["detail"].lower()


@pytest.mark.unit
def test_delete_503_checked_before_confirm():
    """The 503 fires even when the confirm token is wrong — unconfigured is unconfigured."""
    c = _client(deleter=None)
    r = c.post("/account/delete", headers=H_A, json={"confirm": "wrong"})
    assert r.status_code == 503


# --------------------------------------------------------------------------- auth

@pytest.mark.unit
def test_delete_requires_bearer():
    """No auth header -> 401."""
    c = _client(deleter=_full_deleter())
    r = c.post("/account/delete", json={"confirm": "A"})
    assert r.status_code == 401


@pytest.mark.unit
def test_delete_invalid_token_401():
    """An invalid token -> 401 (the verifier raises)."""

    class _RejectAll:
        def verify(self, token):
            raise ValueError("invalid")

    app = FastAPI()
    deps = AccountDeleteDeps(deleter=_full_deleter())
    mount_account_delete(app, deps, make_current_tenant(_RejectAll()))
    c = TestClient(app)
    r = c.post("/account/delete", headers={"Authorization": "Bearer bad"}, json={"confirm": "A"})
    assert r.status_code == 401


# --------------------------------------------------------------------------- confirm guard (422)

@pytest.mark.unit
def test_delete_422_when_confirm_absent():
    """A body without a confirm token -> 422 (accidental-deletion guard)."""
    c = _client(deleter=_full_deleter())
    r = c.post("/account/delete", headers=H_A, json={})
    assert r.status_code == 422


@pytest.mark.unit
def test_delete_422_when_no_body():
    """No body at all -> 422 (never proceeds without an explicit confirm)."""
    c = _client(deleter=_full_deleter())
    r = c.post("/account/delete", headers=H_A)
    assert r.status_code == 422


@pytest.mark.unit
def test_delete_422_when_confirm_wrong_tenant():
    """A confirm token naming a DIFFERENT tenant -> 422. Crucially this is the cross-tenant guard:
    tenant A authenticated, but the body says 'confirm B' — refuse, never delete B (or A)."""
    deleter = _full_deleter()
    c = _client(deleter=deleter)
    r = c.post("/account/delete", headers=H_A, json={"confirm": "B"})
    assert r.status_code == 422
    # The deleter must NOT have been invoked at all.
    assert deleter.calls == []
    # Both tenants' data is untouched.
    assert deleter._rows["A"]["contacts"] == 1
    assert deleter._rows["B"]["contacts"] == 7


@pytest.mark.unit
def test_delete_422_when_confirm_blank():
    """A blank/empty confirm string -> 422."""
    c = _client(deleter=_full_deleter())
    r = c.post("/account/delete", headers=H_A, json={"confirm": ""})
    assert r.status_code == 422


@pytest.mark.unit
def test_delete_422_when_confirm_not_string():
    """A non-string confirm (e.g. a bool/number) -> 422, never a 500."""
    c = _client(deleter=_full_deleter())
    r = c.post("/account/delete", headers=H_A, json={"confirm": True})
    assert r.status_code == 422


# --------------------------------------------------------------------------- successful teardown

@pytest.mark.unit
def test_delete_reports_per_table_counts():
    """A correct confirm tears down the tenant's mutable tables and reports per-table counts."""
    deleter = _full_deleter()
    c = _client(deleter=deleter)
    r = c.post("/account/delete", headers=H_A, json={"confirm": "A"})
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "A"
    # Every deletable table reported with its count (1 each for tenant A).
    for table in DELETABLE_TABLES:
        assert body["deleted"][table] == 1
    assert body["failed"] == {}
    # Only tenant A was targeted.
    assert deleter.calls == ["A"]


@pytest.mark.unit
def test_delete_reports_retained_append_only_tables():
    """Append-only tables are reported under `retained` with a reason — never deleted."""
    c = _client(deleter=_full_deleter())
    r = c.post("/account/delete", headers=H_A, json={"confirm": "A"})
    body = r.json()
    retained = body["retained"]
    # The task's four required append-only tables are all reported retained-with-reason.
    for table in ("onboarding_state", "usage_counters", "cost_events", "support_requests"):
        assert table in retained
        assert isinstance(retained[table], str) and retained[table]
    # And they are NOT in the deleted map.
    for table in retained:
        assert table not in body["deleted"]


@pytest.mark.unit
def test_retained_tables_disjoint_from_deletable():
    """Sanity on the contract itself: no table is both deleted and retained."""
    assert set(DELETABLE_TABLES).isdisjoint(set(RETAINED_TABLES))


# --------------------------------------------------------------------------- idempotency

@pytest.mark.unit
def test_delete_idempotent_second_run_zeros():
    """A second teardown of the same tenant reports zeros (nothing left), still 200."""
    deleter = _full_deleter()
    c = _client(deleter=deleter)

    first = c.post("/account/delete", headers=H_A, json={"confirm": "A"})
    assert first.status_code == 200
    assert first.json()["deleted"]["contacts"] == 1

    second = c.post("/account/delete", headers=H_A, json={"confirm": "A"})
    assert second.status_code == 200
    body = second.json()
    for table in DELETABLE_TABLES:
        assert body["deleted"][table] == 0
    # Retained still reported on the re-run.
    assert "cost_events" in body["retained"]


# --------------------------------------------------------------------------- cross-tenant safety

@pytest.mark.unit
def test_delete_only_targets_claim_tenant():
    """Tenant A's teardown leaves tenant B's data entirely intact."""
    deleter = _full_deleter()
    c = _client(deleter=deleter)
    r = c.post("/account/delete", headers=H_A, json={"confirm": "A"})
    assert r.status_code == 200
    # A's rows are zeroed; B's are untouched.
    assert all(v == 0 for v in deleter._rows["A"].values())
    assert all(v == 7 for v in deleter._rows["B"].values())
    assert deleter.calls == ["A"]


@pytest.mark.unit
def test_two_tenants_tear_down_independently():
    """Each tenant, confirming its own id, tears down only its own data."""
    deleter = _full_deleter()
    c = _client(deleter=deleter)

    ra = c.post("/account/delete", headers=H_A, json={"confirm": "A"})
    rb = c.post("/account/delete", headers=H_B, json={"confirm": "B"})
    assert ra.status_code == 200 and rb.status_code == 200
    assert ra.json()["tenant_id"] == "A"
    assert rb.json()["tenant_id"] == "B"
    assert ra.json()["deleted"]["deals"] == 1
    assert rb.json()["deleted"]["deals"] == 7
    assert deleter.calls == ["A", "B"]


@pytest.mark.unit
def test_tenant_from_claim_not_from_query():
    """A query param trying to redirect the teardown is ignored — tenant comes from the claim."""
    deleter = _full_deleter()
    c = _client(deleter=deleter)
    r = c.post("/account/delete?tenant_id=B", headers=H_A, json={"confirm": "A"})
    assert r.status_code == 200
    assert r.json()["tenant_id"] == "A"
    assert deleter.calls == ["A"]


# --------------------------------------------------------------------------- per-table failure isolation

@pytest.mark.unit
def test_delete_reports_failed_table_without_aborting_rest():
    """If one table's DELETE fails, it is reported under `failed` and the rest still delete."""
    deleter = FakeDeleter(
        rows={"A": {t: 1 for t in DELETABLE_TABLES}},
        fail_tables={"deals"},
    )
    c = _client(deleter=deleter)
    r = c.post("/account/delete", headers=H_A, json={"confirm": "A"})
    assert r.status_code == 200
    body = r.json()
    assert "deals" in body["failed"]
    assert "deals" not in body["deleted"]
    # Every other deletable table still reported a count.
    for table in DELETABLE_TABLES:
        if table == "deals":
            continue
        assert body["deleted"][table] == 1


# --------------------------------------------------------------------------- connector-secret purge

class FakeVault:
    """SecretWriter-shaped fake for the purge path: holds refs, optionally explodes."""

    def __init__(self, refs=(), explode_on=()):
        self.refs = set(refs)
        self.explode_on = set(explode_on)
        self.deleted: list[str] = []

    def put_secret(self, ref, value):  # pragma: no cover — purge never writes
        raise AssertionError("purge must never write a secret")

    def secret_exists(self, ref):  # pragma: no cover — purge never reads status
        raise AssertionError("purge must never read status")

    def delete_secret(self, ref) -> bool:
        if ref in self.explode_on:
            raise RuntimeError("simulated vault outage")
        self.deleted.append(ref)
        if ref in self.refs:
            self.refs.discard(ref)
            return True
        return False


@pytest.mark.unit
def test_delete_purges_connector_secrets_for_claims_tenant_only():
    """The purge derives every ref from the CLAIM tenant and reports only slots
    that actually existed (hubspot+stripe vaulted; gohighlevel absent)."""
    vault = FakeVault(refs={"uplift/A/hubspot", "uplift/A/stripe", "uplift/B/hubspot"})
    c = _client(deleter=_full_deleter(), secret_writer=vault)
    r = c.post("/account/delete", headers=H_A, json={"confirm": "A"})
    assert r.status_code == 200
    body = r.json()["connector_secrets"]
    assert body["status"] == "purged"
    assert sorted(body["purged"]) == ["hubspot", "stripe"]
    assert body["failed"] == []
    # Tenant B's slot was never touched (every attempted ref names tenant A).
    assert "uplift/B/hubspot" in vault.refs
    assert all(ref.startswith("uplift/A/") for ref in vault.deleted)


@pytest.mark.unit
def test_delete_reports_skipped_unconfigured_without_writer():
    """No vault writer wired -> the response says so honestly (never a fake purge)."""
    c = _client(deleter=_full_deleter(), secret_writer=None)
    r = c.post("/account/delete", headers=H_A, json={"confirm": "A"})
    assert r.status_code == 200
    assert r.json()["connector_secrets"] == {
        "purged": [], "failed": [], "status": "skipped_unconfigured"}


@pytest.mark.unit
def test_delete_purge_failure_reported_never_aborts_response():
    """A vault outage on one slot lands in `failed` (status partial) — the PG
    teardown already committed, so the response must still be a 200 report."""
    vault = FakeVault(refs={"uplift/A/hubspot", "uplift/A/stripe"},
                      explode_on={"uplift/A/stripe"})
    c = _client(deleter=_full_deleter(), secret_writer=vault)
    r = c.post("/account/delete", headers=H_A, json={"confirm": "A"})
    assert r.status_code == 200
    body = r.json()["connector_secrets"]
    assert body["status"] == "partial"
    assert body["purged"] == ["hubspot"]
    assert body["failed"] == ["stripe"]
