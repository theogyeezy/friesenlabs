"""Unit: the Aurora-backed PRE-TENANT signup stores (signup/store_pg.py) — mocked psycopg2.

Proves the three contracts the signup flow leans on, with a fake pool (no DB):
  * accounts/stripe_events are deliberately RLS-EXEMPT pre-tenant tables — the stores issue NO
    `SET LOCAL app.current_tenant` (there is no tenant to bind yet) and NO session-level SET;
  * idempotency: account insert is `ON CONFLICT (id) DO NOTHING`; the stripe_events claim is
    ONE atomic statement (insert, or re-claim of a released tombstone) with the won/lost outcome
    surfaced to the caller — and release is an UPDATE tombstone, never a DELETE (REQ-002 grants);
  * jsonb merge ATOMICITY: account updates merge meta (`meta || %s::jsonb`, never `meta = %s`)
    and the OTP write is ONE statement (`meta || jsonb_build_object('otp', %s::jsonb)`) — no
    read-modify-write window, so the two meta writers can never clobber each other.
"""
import pytest

import psycopg2
import psycopg2.pool
from psycopg2.extras import Json

from signup.accounts import Account, State
from signup.store_pg import (
    PgAccountStore,
    PgOtpStore,
    PgStripeEventLedger,
    _account_meta,
    _row_to_account,
)


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self.rowcount = 0

    def execute(self, sql, params=None):
        self._conn.log.append((" ".join(sql.split()), params))
        self.rowcount = self._conn.rowcounts.pop(0) if self._conn.rowcounts else 1

    def fetchone(self):
        return self._conn.results.pop(0) if self._conn.results else None

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self):
        self.log: list = []        # (normalized sql, params) per execute
        self.results: list = []    # queued fetchone() returns
        self.rowcounts: list = []  # queued rowcount per execute
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakePool:
    def __init__(self, minconn, maxconn, dsn):
        self.conn = FakeConn()

    def getconn(self):
        return self.conn

    def putconn(self, conn):
        pass


@pytest.fixture
def patched(monkeypatch):
    pool = FakePool(1, 10, None)
    monkeypatch.setattr(
        psycopg2.pool, "ThreadedConnectionPool", lambda minc, maxc, dsn: pool
    )
    return pool


def _sql(pool):
    return [s for s, _ in pool.conn.log]


def _acct(**over):
    base = dict(id="11111111-1111-1111-1111-111111111111", email="a@b.co", phone="+15125550100",
                cognito_sub="sub-1", state=State.CREATED, email_verified=False,
                phone_verified=False, stripe_customer_id=None, tenant_id=None,
                meta={"plan": "pro"})
    base.update(over)
    return Account(**base)


DSN = "postgresql://crm_app@h/db"


# ---------------------------------------------------------------------------
# PgAccountStore
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_account_store_is_pre_tenant_no_set_local(patched):
    """accounts is RLS-EXEMPT (pre-tenant): NO tenant GUC bind of any kind is ever issued."""
    store = PgAccountStore(DSN)
    patched.conn.results = [None]
    store.get("11111111-1111-1111-1111-111111111111")
    store.insert(_acct())
    store.update(_acct(state=State.PAID))
    patched.conn.results = [None]
    store.get_by_email("a@b.co")
    assert not any("app.current_tenant" in s for s in _sql(patched))
    assert not any(s.startswith("SET") for s in _sql(patched))


@pytest.mark.unit
def test_get_by_stripe_customer_id_queries_meta_no_tenant_bind(patched):
    """The invoice.paid fallback resolver: a jsonb meta lookup, still pre-tenant (no SET LOCAL)."""
    store = PgAccountStore(DSN)
    patched.conn.results = [{
        "id": "11111111-1111-1111-1111-111111111111",
        "email": "a@b.co", "phone": "+15125550100", "status": "paid",
        "plan": "pro", "tenant_id": None,
        "meta": {"cognito_sub": "sub-1", "email_verified": True, "phone_verified": True,
                 "stripe_customer_id": "cus_42", "account": {}},
    }]
    acct = store.get_by_stripe_customer_id("cus_42")
    assert acct is not None and acct.stripe_customer_id == "cus_42"
    sql, params = patched.conn.log[0]
    assert "meta->>'stripe_customer_id' = %s" in sql
    assert params == ("cus_42",)
    assert not any("app.current_tenant" in s for s in _sql(patched))  # RLS-EXEMPT (pre-tenant)
    # And a miss is an honest None.
    patched.conn.results = [None]
    assert store.get_by_stripe_customer_id("cus_nope") is None


@pytest.mark.unit
def test_account_insert_is_idempotent_on_id(patched):
    store = PgAccountStore(DSN)
    store.insert(_acct())
    sql, params = patched.conn.log[0]
    assert "INSERT INTO accounts" in sql
    assert "ON CONFLICT (id) DO NOTHING" in sql   # raced re-submission never raises
    assert isinstance(params[-1], Json)           # meta rides as jsonb, properly adapted
    assert params[0] == "11111111-1111-1111-1111-111111111111"
    assert params[4] == "pro"                     # plan column lifted from meta


@pytest.mark.unit
def test_account_update_merges_meta_never_replaces(patched):
    """meta = meta || %s::jsonb — an account write must not clobber PgOtpStore's 'otp' key."""
    store = PgAccountStore(DSN)
    store.update(_acct(state=State.PAID, stripe_customer_id="cus_1"))
    sql, params = patched.conn.log[0]
    assert "UPDATE accounts SET" in sql
    assert "meta = meta || %s::jsonb" in sql
    assert "meta = %s" not in sql.replace("meta = meta ||", "")  # never a flat replace
    assert "updated_at = now()" in sql
    merged = params[5].adapted                    # the Json-wrapped merge document
    assert merged["stripe_customer_id"] == "cus_1"
    assert merged["account"] == {"plan": "pro"}
    assert "otp" not in merged                    # the account writer never touches the OTP key


@pytest.mark.unit
def test_settle_paid_atomic_is_one_cas_statement(patched):
    """The race fix: a SINGLE UPDATE .. WHERE status NOT IN (settled) RETURNING * — only the call
    that flips the row gets the Account back (won); a row already settled returns None (lost)."""
    store = PgAccountStore(DSN)
    # Win: the UPDATE matched a row and RETURNING * came back.
    patched.conn.results = [{
        "id": "11111111-1111-1111-1111-111111111111",
        "email": "a@b.co", "phone": "+15125550100", "status": "paid",
        "plan": "pro", "tenant_id": None,
        "meta": {"cognito_sub": "sub-1", "email_verified": True, "phone_verified": True,
                 "stripe_customer_id": "cus_42", "account": {}},
    }]
    won = store.settle_paid_atomic("11111111-1111-1111-1111-111111111111")
    assert won is not None and won.state is State.PAID
    sql, params = patched.conn.log[0]
    assert "UPDATE accounts SET status = %s" in sql
    assert "WHERE id = %s AND status NOT IN (%s, %s, %s)" in sql
    assert "RETURNING *" in sql
    assert params[0] == "paid"
    assert params[2:5] == ("paid", "provisioning", "active")   # the already-settled guard set
    assert len(patched.conn.log) == 1                          # ONE statement, no read-then-write
    assert not any("app.current_tenant" in s for s in _sql(patched))  # RLS-EXEMPT (pre-tenant)
    # Lost: no row returned (already settled) -> None.
    patched.conn.results = [None]
    assert store.settle_paid_atomic("11111111-1111-1111-1111-111111111111") is None


@pytest.mark.unit
def test_checkout_intent_round_trip_is_atomic_merge(patched):
    """save_checkout_intent persists the SERVER-known checkout facts under meta.checkout_intent via
    the SAME one-statement merge the OTP writer uses (no read-modify-write window); get reads it
    back. The signed webhook is verified against this intent before settlement."""
    store = PgAccountStore(DSN)
    intent = {"checkout_id": "cs_42", "customer": "cus_42", "plan": "team",
              "price_id": "price_team", "mode": "subscription", "livemode": False}
    store.save_checkout_intent("11111111-1111-1111-1111-111111111111", intent)
    assert len(patched.conn.log) == 1                          # one atomic statement
    sql, params = patched.conn.log[0]
    assert "meta = meta || jsonb_build_object('checkout_intent', %s::jsonb)" in sql
    assert isinstance(params[0], Json)
    assert params[0].adapted["price_id"] == "price_team"
    assert not any("app.current_tenant" in s for s in _sql(patched))  # RLS-EXEMPT (pre-tenant)
    # read-back
    patched.conn.results = [{"ci": dict(intent)}]
    assert store.get_checkout_intent("11111111-1111-1111-1111-111111111111") == intent
    assert any("SELECT meta->'checkout_intent' AS ci FROM accounts" in s for s in _sql(patched))
    # absent intent -> honest None
    patched.conn.results = [{"ci": None}]
    assert store.get_checkout_intent("22222222-2222-2222-2222-222222222222") is None


@pytest.mark.unit
def test_account_get_round_trips_row_to_account(patched):
    store = PgAccountStore(DSN)
    patched.conn.results = [{
        "id": "11111111-1111-1111-1111-111111111111",
        "email": "a@b.co", "phone": "+15125550100", "status": "phone_verified",
        "plan": "pro", "tenant_id": None,
        "meta": {"cognito_sub": "sub-1", "email_verified": True, "phone_verified": True,
                 "stripe_customer_id": "cus_1", "account": {"plan": "pro"},
                 "otp": {"code_hmac": "x"}},   # OTP key present — must NOT leak into Account.meta
    }]
    acct = store.get("11111111-1111-1111-1111-111111111111")
    assert isinstance(acct, Account)
    assert acct.state is State.PHONE_VERIFIED
    assert acct.fully_verified and acct.may_pay   # VERIFY-BEFORE-PAY reads survive the round trip
    assert acct.cognito_sub == "sub-1"
    assert acct.stripe_customer_id == "cus_1"
    assert acct.meta == {"plan": "pro"}           # the free-form dict, not the raw jsonb envelope
    assert acct.tenant_id is None                 # pre-tenant


@pytest.mark.unit
def test_account_get_by_email_and_missing_rows(patched):
    store = PgAccountStore(DSN)
    patched.conn.results = [None, None]
    assert store.get("22222222-2222-2222-2222-222222222222") is None
    assert store.get_by_email("nobody@b.co") is None
    sql = _sql(patched)
    assert any("WHERE id = %s" in s for s in sql)
    assert any("WHERE email = %s" in s for s in sql)


@pytest.mark.unit
def test_account_meta_round_trip_pure_mapping():
    """_account_meta -> _row_to_account is lossless for the fields Account carries."""
    acct = _acct(state=State.PAID, email_verified=True, phone_verified=True,
                 stripe_customer_id="cus_9", tenant_id="33333333-3333-3333-3333-333333333333")
    row = {"id": acct.id, "email": acct.email, "phone": acct.phone, "status": acct.state.value,
           "plan": "pro", "tenant_id": acct.tenant_id, "meta": _account_meta(acct)}
    back = _row_to_account(row)
    assert back == acct


# ---------------------------------------------------------------------------
# PgStripeEventLedger
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_ledger_mark_handled_is_atomic_claim(patched):
    store = PgStripeEventLedger(DSN)
    patched.conn.rowcounts = [1, 0]
    assert store.mark_handled("evt_1", "11111111-1111-1111-1111-111111111111") is True
    assert store.mark_handled("evt_1") is False   # re-delivery loses the claim -> already handled
    sql, params = patched.conn.log[0]
    assert "INSERT INTO stripe_events" in sql
    # ONE statement: fresh insert OR atomic re-claim of a row a failed attempt released —
    # an actively-held claim matches the conflict but NOT the WHERE, touching no row (rowcount 0).
    assert "ON CONFLICT (event_id) DO UPDATE" in sql
    assert "SET released_at = NULL" in sql
    assert "WHERE stripe_events.released_at IS NOT NULL" in sql
    assert params[0] == "evt_1"
    assert not any("app.current_tenant" in s for s in _sql(patched))  # RLS-EXEMPT (pre-tenant)


@pytest.mark.unit
def test_ledger_release_is_a_tombstone_not_a_delete(patched):
    """REQ-002 grants crm_app no DELETE on stripe_events (append-only ledger): releasing a claim
    after a failed attempt must be an UPDATE setting released_at, never a DELETE."""
    store = PgStripeEventLedger(DSN)
    store.release("evt_1")
    sql, params = patched.conn.log[0]
    assert sql.startswith("UPDATE stripe_events SET released_at = now()")
    assert "DELETE" not in sql
    assert params == ("evt_1",)
    assert not any("DELETE" in s for s in _sql(patched))


@pytest.mark.unit
def test_ledger_is_handled_ignores_released_rows(patched):
    store = PgStripeEventLedger(DSN)
    patched.conn.results = [{"?column?": 1}, None]
    assert store.is_handled("evt_1") is True
    assert store.is_handled("evt_2") is False     # absent OR tombstoned -> retryable
    assert any(
        "SELECT 1 FROM stripe_events WHERE event_id = %s AND released_at IS NULL" in s
        for s in _sql(patched)
    )


# ---------------------------------------------------------------------------
# PgOtpStore
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_otp_put_is_one_atomic_merge_statement(patched):
    """put_otp must be a SINGLE UPDATE doing the merge inside Postgres — no read-modify-write
    window for a concurrent account update to clobber."""
    store = PgOtpStore(DSN)
    store.put_otp("11111111-1111-1111-1111-111111111111",
                  {"code_hmac": "h", "expires_at": 1, "attempts": 0})
    assert len(patched.conn.log) == 1             # exactly one statement: no SELECT-then-UPDATE
    sql, params = patched.conn.log[0]
    assert "UPDATE accounts SET" in sql
    assert "meta = meta || jsonb_build_object('otp', %s::jsonb)" in sql
    assert isinstance(params[0], Json)
    assert params[0].adapted["code_hmac"] == "h"
    assert not any("app.current_tenant" in s for s in _sql(patched))  # RLS-EXEMPT (pre-tenant)


@pytest.mark.unit
def test_otp_get_and_clear(patched):
    store = PgOtpStore(DSN)
    patched.conn.results = [{"otp": {"code_hmac": "h", "attempts": 2}}, None, {"otp": None}]
    assert store.get_otp("11111111-1111-1111-1111-111111111111") == {"code_hmac": "h", "attempts": 2}
    assert store.get_otp("22222222-2222-2222-2222-222222222222") is None  # no row
    assert store.get_otp("33333333-3333-3333-3333-333333333333") is None  # row, no OTP
    store.clear_otp("11111111-1111-1111-1111-111111111111")
    assert any("meta = meta - 'otp'" in s for s in _sql(patched))
    assert any("SELECT meta->'otp' AS otp FROM accounts" in s for s in _sql(patched))


@pytest.mark.unit
def test_otp_store_satisfies_tokens_protocol(patched):
    """PgOtpStore plugs straight into OtpService (the seam tokens.py defines)."""
    from signup.tokens import OtpService

    store = PgOtpStore(DSN)
    svc = OtpService("s3cret", store, now=lambda: 1_000_000.0)
    patched.conn.results = [None]                 # no prior record -> fresh window
    code = svc.issue("11111111-1111-1111-1111-111111111111")
    assert len(code) == 6 and code.isdigit()
    put_sql = [s for s in _sql(patched) if "jsonb_build_object" in s]
    assert len(put_sql) == 1                      # the issue persisted via the atomic merge


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_tx_rolls_back_and_returns_conn_on_error(patched, monkeypatch):
    store = PgAccountStore(DSN)

    def boom(self, sql, params=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(FakeCursor, "execute", boom)
    with pytest.raises(RuntimeError):
        store.get("11111111-1111-1111-1111-111111111111")
    assert patched.conn.rollbacks == 1
    assert patched.conn.commits == 0


# ---------------------------------------------------------------------------
# Billing portal additions (get_by_tenant_id + billing status meta)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_get_by_tenant_id_resolves_account_no_tenant_bind(patched):
    """The billing-portal resolver: find the account by its provisioner-minted tenant_id (the
    verified claim). A plain WHERE tenant_id lookup — still RLS-EXEMPT (no SET LOCAL)."""
    store = PgAccountStore(DSN)
    patched.conn.results = [{
        "id": "11111111-1111-1111-1111-111111111111",
        "email": "a@b.co", "phone": "+15125550100", "status": "active",
        "plan": "team", "tenant_id": "tenant-9",
        "meta": {"cognito_sub": "sub-1", "email_verified": True, "phone_verified": True,
                 "stripe_customer_id": "cus_42", "account": {"plan": "team"}},
    }]
    acct = store.get_by_tenant_id("tenant-9")
    assert acct is not None and acct.tenant_id == "tenant-9"
    assert acct.stripe_customer_id == "cus_42"
    sql, params = patched.conn.log[0]
    assert "WHERE tenant_id = %s" in sql
    assert params == ("tenant-9",)
    assert not any("app.current_tenant" in s for s in _sql(patched))
    # A miss is an honest None.
    patched.conn.results = [None]
    assert store.get_by_tenant_id("tenant-nope") is None


@pytest.mark.unit
def test_set_billing_status_is_one_atomic_merge(patched):
    """The cancellation webhook flips billing status via the SAME atomic jsonb_build_object merge
    the OTP/intent writers use — no read-modify-write window, never a flat `meta = %s`."""
    store = PgAccountStore(DSN)
    store.set_billing_status("11111111-1111-1111-1111-111111111111", "canceled",
                             reason="customer.subscription.deleted")
    sql, params = patched.conn.log[0]
    assert "UPDATE accounts SET" in sql
    assert "meta = meta || jsonb_build_object('billing_status', %s::jsonb)" in sql
    assert "updated_at = now()" in sql
    rec = params[0].adapted
    assert rec == {"status": "canceled", "reason": "customer.subscription.deleted"}
    assert params[1] == "11111111-1111-1111-1111-111111111111"
    assert not any("app.current_tenant" in s for s in _sql(patched))


@pytest.mark.unit
def test_get_billing_status_reads_meta_key(patched):
    store = PgAccountStore(DSN)
    patched.conn.results = [{"bs": {"status": "past_due", "reason": "customer.subscription.updated"}}]
    rec = store.get_billing_status("11111111-1111-1111-1111-111111111111")
    assert rec == {"status": "past_due", "reason": "customer.subscription.updated"}
    sql, _ = patched.conn.log[0]
    assert "meta->'billing_status'" in sql
    # Absent -> honest None (a never-cancelled account has no billing_status key).
    patched.conn.results = [{"bs": None}]
    assert store.get_billing_status("11111111-1111-1111-1111-111111111111") is None
