"""Aurora-backed PRE-TENANT signup stores: accounts, the stripe_events ledger, OTP-in-meta.

Replaces the in-memory `api/prod_deps._AccountStore` (TODO INT/P0): with 2 Fargate tasks, a dict
store means the account created on one task is invisible to the webhook landing on the other, and
everything is lost on restart. These stores put that state in Aurora. (The prod_deps wiring is a
follow-up PR — nothing here is mounted yet.)

WHY THERE IS NO `SET LOCAL app.current_tenant` HERE (deliberate — read before "fixing"):
`accounts` and `stripe_events` are RLS-EXEMPT pre-tenant tables (see db/schema.sql): a signup row
exists BEFORE any tenant_id is provisioned (tenant_id is NULL until the Provisioner mints it at
Step 55), so there is no tenant to bind and no tenant_isolation policy on these tables to satisfy.
Access control is the crm_app GRANT surface (infra/REQUESTS.md REQ-002), not RLS. THE TRUST RULE
still holds in spirit: these stores never accept a tenant_id from env/header/body — the only
tenant_id ever written is the one the Provisioner sets on the Account object after the signed
Stripe webhook. Every TENANT-scoped table keeps the full PgApprovalStore pattern
(pooled per-op conn + `SET LOCAL app.current_tenant` in one transaction).

Connection discipline is otherwise IDENTICAL to api/control/greenlight.py `PgApprovalStore` /
agents/workspace_store.py `PgWorkspaceStore`: connect as the non-owner crm_app role; each
operation checks a connection out of a thread-safe pool and runs in ONE transaction
(commit on success / rollback on error / always returned) — never a connection shared across
threads, never session-level state.

accounts.meta jsonb layout (one column, two writers, NO clobbering):
  * account-flow keys — cognito_sub, email_verified, phone_verified, stripe_customer_id, and the
    Account dataclass's free-form dict under 'account' — written by PgAccountStore via the ATOMIC
    jsonb merge `meta = meta || %s::jsonb` (top-level keys it owns are replaced; keys it does not
    own survive);
  * 'otp' — the in-flight OTP record, written ONLY by PgOtpStore via the single-statement merge
    `meta = meta || jsonb_build_object('otp', %s::jsonb)` (no read-modify-write window).
An account update can therefore never erase a concurrently-issued OTP, and vice versa.

Import-safe: psycopg2 is imported lazily on construction; importing this module needs no driver
and no network.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager

from .accounts import Account, State


class _PgBase:
    """Shared pool plumbing (mirrors PgApprovalStore/PgWorkspaceStore, minus the tenant bind —
    see the module docstring for why these pre-tenant tables have no SET LOCAL)."""

    def __init__(self, dsn: str):
        import psycopg2  # noqa: PLC0415 — guarded (import-safe module)
        import psycopg2.pool  # noqa: PLC0415
        from psycopg2.extras import Json, RealDictCursor  # noqa: PLC0415
        self._psycopg2 = psycopg2
        self._Json = Json
        self._cursor_factory = RealDictCursor
        pool_max = int(os.environ.get("UPLIFT_DB_POOL_MAX", "10"))
        # min == max: a fixed-size pool RETAINS returned connections (psycopg2 closes any
        # connection beyond minconn on putconn), avoiding TCP/auth churn under concurrent load.
        self._pool = psycopg2.pool.ThreadedConnectionPool(pool_max, pool_max, dsn)

    def _getconn(self):
        """Check out a pooled connection, waiting briefly if the pool is momentarily exhausted."""
        deadline = time.monotonic() + 10.0
        while True:
            try:
                return self._pool.getconn()
            except self._psycopg2.pool.PoolError as exc:
                if "exhausted" not in str(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.005)

    @contextmanager
    def _tx(self):
        """Yield a RealDict cursor inside ONE transaction on a per-op pooled connection.

        No `SET LOCAL app.current_tenant`: accounts/stripe_events are RLS-EXEMPT pre-tenant
        tables — there is no tenant to bind (module docstring). Commit on success, rollback on
        error, always return the connection to the pool; never shared across threads.
        """
        conn = self._getconn()
        try:
            cur = conn.cursor(cursor_factory=self._cursor_factory)
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)


# --- Account <-> row mapping -------------------------------------------------
# The table keeps the flow-critical columns relational (id/email/phone/status/plan/tenant_id);
# everything else the Account dataclass carries rides in meta jsonb (layout: module docstring).

def _account_meta(acct: Account) -> dict:
    return {
        "cognito_sub": acct.cognito_sub,
        "email_verified": bool(acct.email_verified),
        "phone_verified": bool(acct.phone_verified),
        "stripe_customer_id": acct.stripe_customer_id,
        "account": dict(acct.meta or {}),
    }


def _row_to_account(row: dict) -> Account:
    meta = dict(row.get("meta") or {})
    return Account(
        id=str(row["id"]),
        email=row.get("email"),
        phone=row.get("phone"),
        cognito_sub=meta.get("cognito_sub", ""),
        state=State(row["status"]) if row.get("status") else State.CREATED,
        email_verified=bool(meta.get("email_verified", False)),
        phone_verified=bool(meta.get("phone_verified", False)),
        stripe_customer_id=meta.get("stripe_customer_id"),
        tenant_id=str(row["tenant_id"]) if row.get("tenant_id") else None,
        meta=dict(meta.get("account") or {}),
    )


class PgAccountStore(_PgBase):
    """Aurora-backed account store over the pre-tenant `accounts` table (as crm_app).

    Implements the store seam AccountService/PaymentService/Provisioner already use:
    get / get_by_email / insert / update, over `signup.accounts.Account` objects.
    """

    def get(self, account_id: str) -> Account | None:
        with self._tx() as cur:
            cur.execute("SELECT * FROM accounts WHERE id = %s", (str(account_id),))
            row = cur.fetchone()
        return _row_to_account(dict(row)) if row else None

    def get_by_email(self, email: str) -> Account | None:
        # Callers pass the normalize_email()'d (lowercased) address; the UNIQUE index makes this
        # the duplicate-signup guard AccountService.create relies on.
        with self._tx() as cur:
            cur.execute("SELECT * FROM accounts WHERE email = %s", (email,))
            row = cur.fetchone()
        return _row_to_account(dict(row)) if row else None

    def insert(self, acct: Account) -> None:
        # ON CONFLICT (id) DO NOTHING: AccountService.create is idempotent by account_id; a raced
        # re-submission must not raise. A raced DUPLICATE EMAIL still surfaces as the unique-
        # constraint error (correct: two different ids may never share an email).
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO accounts (id, email, phone, status, plan, tenant_id, meta) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb) "
                "ON CONFLICT (id) DO NOTHING",
                (str(acct.id), acct.email, acct.phone, acct.state.value,
                 (acct.meta or {}).get("plan"), acct.tenant_id, self._Json(_account_meta(acct))),
            )

    def update(self, acct: Account) -> None:
        # meta is MERGED (`meta || %s::jsonb`), never replaced: PgAccountStore owns its top-level
        # keys; the 'otp' key (owned by PgOtpStore) survives an account write (module docstring).
        with self._tx() as cur:
            cur.execute(
                "UPDATE accounts SET email = %s, phone = %s, status = %s, plan = %s, "
                "tenant_id = %s, meta = meta || %s::jsonb, updated_at = now() "
                "WHERE id = %s",
                (acct.email, acct.phone, acct.state.value, (acct.meta or {}).get("plan"),
                 acct.tenant_id, self._Json(_account_meta(acct)), str(acct.id)),
            )


class PgStripeEventLedger(_PgBase):
    """Webhook idempotency ledger over `stripe_events` (TODO P1: survive restarts / 2 tasks).

    `mark_handled` is the atomic CLAIM: INSERT .. ON CONFLICT (event_id) DO NOTHING returns
    True iff THIS call inserted the row — a re-delivered event (same Stripe event id) on any
    task loses the insert and short-circuits before any state change.
    """

    def is_handled(self, event_id: str) -> bool:
        with self._tx() as cur:
            cur.execute("SELECT 1 FROM stripe_events WHERE event_id = %s", (str(event_id),))
            return cur.fetchone() is not None

    def mark_handled(self, event_id: str, account_id: str | None = None) -> bool:
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO stripe_events (event_id, account_id) VALUES (%s,%s) "
                "ON CONFLICT (event_id) DO NOTHING",
                (str(event_id), account_id),
            )
            return cur.rowcount == 1   # True = we claimed it; False = someone already had


class PgOtpStore(_PgBase):
    """`signup.tokens.OtpStore` over accounts.meta jsonb — every write is ONE atomic statement.

    `put_otp` is `meta || jsonb_build_object('otp', %s::jsonb)`: the merge happens inside
    Postgres, so there is no read-modify-write window for a concurrent account update (which
    itself merges, never replaces) to clobber — and vice versa.
    """

    def get_otp(self, account_id: str) -> dict | None:
        with self._tx() as cur:
            cur.execute("SELECT meta->'otp' AS otp FROM accounts WHERE id = %s",
                        (str(account_id),))
            row = cur.fetchone()
        rec = row.get("otp") if row else None
        return dict(rec) if rec else None

    def put_otp(self, account_id: str, record: dict) -> None:
        with self._tx() as cur:
            cur.execute(
                "UPDATE accounts SET "
                "meta = meta || jsonb_build_object('otp', %s::jsonb), updated_at = now() "
                "WHERE id = %s",
                (self._Json(dict(record)), str(account_id)),
            )

    def clear_otp(self, account_id: str) -> None:
        with self._tx() as cur:
            cur.execute(
                "UPDATE accounts SET meta = meta - 'otp', updated_at = now() WHERE id = %s",
                (str(account_id),),
            )
