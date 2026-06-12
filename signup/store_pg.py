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
    `meta = meta || jsonb_build_object('otp', %s::jsonb)` (no read-modify-write window);
  * 'used_email_tokens' — the consumed email-token nonces ({nonce: expires_at}), written ONLY by
    PgUsedTokenStore via a single statement that prunes expired nonces and adds the new one with
    the merge computed inside Postgres (no read-modify-write window, bounded growth).
An account update can therefore never erase a concurrently-issued OTP or a consumed-token
record, and vice versa.

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
        self._pool = psycopg2.pool.ThreadedConnectionPool(1, pool_max, dsn)

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

    def get_by_stripe_customer_id(self, customer_id: str) -> Account | None:
        # The `invoice.paid` fallback (signup/payment.py _resolve_account): invoices carry no
        # client_reference_id, so the mapping start_checkout persisted (meta.stripe_customer_id)
        # is the last-resort account resolver. The value compared is the one WE wrote from
        # Stripe's create-customer response — never client input.
        with self._tx() as cur:
            cur.execute("SELECT * FROM accounts WHERE meta->>'stripe_customer_id' = %s",
                        (str(customer_id),))
            row = cur.fetchone()
        return _row_to_account(dict(row)) if row else None

    def get_by_tenant_id(self, tenant_id: str) -> Account | None:
        # The BILLING-PORTAL resolver (api/billing_routes.py): the authed request carries only the
        # verified Cognito custom:tenant_id claim — the provisioner-minted tenant_id stamped on the
        # account row at Step 55 — so the account (and its stripe_customer_id) is found by tenant_id,
        # never by account_id from the client. tenant_id is UNIQUE per provisioned account.
        with self._tx() as cur:
            cur.execute("SELECT * FROM accounts WHERE tenant_id = %s", (str(tenant_id),))
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

    def save_checkout_intent(self, account_id: str, intent: dict) -> None:
        # Persist the SERVER-known facts about a started checkout (session id, customer, price,
        # plan, livemode) under the meta `checkout_intent` key via the same atomic single-statement
        # merge the OTP writer uses — no read-modify-write window, and PgAccountStore's other
        # top-level keys (and 'otp') survive untouched. The signed webhook is verified against this
        # intent before settlement (a valid signature does not prove the payload's
        # amount/price/livemode/customer match what we requested).
        with self._tx() as cur:
            cur.execute(
                "UPDATE accounts SET "
                "meta = meta || jsonb_build_object('checkout_intent', %s::jsonb), "
                "updated_at = now() WHERE id = %s",
                (self._Json(dict(intent)), str(account_id)),
            )

    def get_checkout_intent(self, account_id: str) -> dict | None:
        with self._tx() as cur:
            cur.execute("SELECT meta->'checkout_intent' AS ci FROM accounts WHERE id = %s",
                        (str(account_id),))
            row = cur.fetchone()
        rec = row.get("ci") if row else None
        return dict(rec) if rec else None

    def set_billing_status(self, account_id: str, status: str, *, reason: str = "") -> None:
        # Persist the post-checkout subscription lifecycle state (active / past_due / canceled)
        # the cancellation webhook flips, via the SAME atomic single-statement merge the OTP +
        # checkout-intent writers use — no read-modify-write window, and PgAccountStore's other
        # top-level keys (and 'otp') survive untouched. Idempotent: re-writing the same status is
        # a harmless no-op overwrite. The app reads it back via get_billing_status so a cancelled
        # tenant can be reflected in a grace/cancelled state. NOTE: this does NOT touch the
        # account `status` column (the signup-lifecycle State machine) — billing lifecycle and
        # provisioning lifecycle are deliberately separate axes (an ACTIVE provisioned tenant whose
        # subscription later cancels stays ACTIVE-but-billing-canceled).
        record = {"status": str(status), "reason": str(reason)}
        with self._tx() as cur:
            cur.execute(
                "UPDATE accounts SET "
                "meta = meta || jsonb_build_object('billing_status', %s::jsonb), "
                "updated_at = now() WHERE id = %s",
                (self._Json(record), str(account_id)),
            )

    def get_billing_status(self, account_id: str) -> dict | None:
        with self._tx() as cur:
            cur.execute("SELECT meta->'billing_status' AS bs FROM accounts WHERE id = %s",
                        (str(account_id),))
            row = cur.fetchone()
        rec = row.get("bs") if row else None
        return dict(rec) if rec else None

    def settle_paid_atomic(self, account_id: str) -> Account | None:
        """Atomically flip a NOT-yet-settled account to PAID, returning the row iff THIS call won.

        The single-statement CAS — ``UPDATE .. SET status='paid' WHERE id=%s AND status NOT IN
        ('paid','provisioning','active') RETURNING *`` — is the primary guard against the
        double-provision race: Stripe sends BOTH checkout.session.completed and invoice.paid for
        one purchase (DIFFERENT event ids), so the per-event stripe_events claim cannot serialize
        them. Exactly one of N concurrent settlements flips the row and gets the Account back; every
        other observes the already-settled state and gets None (a safe idempotent no-op). The
        stripe_events claim stays as a SECOND layer (cross-task replay of the SAME id)."""
        with self._tx() as cur:
            cur.execute(
                "UPDATE accounts SET status = %s, updated_at = now() "
                "WHERE id = %s AND status NOT IN (%s, %s, %s) RETURNING *",
                (State.PAID.value, str(account_id),
                 State.PAID.value, State.PROVISIONING.value, State.ACTIVE.value),
            )
            row = cur.fetchone()
        return _row_to_account(dict(row)) if row else None


class PgStripeEventLedger(_PgBase):
    """Webhook idempotency ledger over `stripe_events` (TODO P1: survive restarts / 2 tasks).

    `mark_handled` is the atomic CLAIM (PaymentService takes it BEFORE doing the work): one
    statement, True iff THIS call took the claim — of N tasks racing the same Stripe event id,
    exactly one gets True; every other returns False and must no-op.

    `release` gives a claim back after a FAILED attempt so the event stays retryable. It is a
    TOMBSTONE (`released_at` set), NOT a DELETE: the crm_app grant surface on stripe_events is
    append-only — SELECT/INSERT/UPDATE, deliberately no DELETE (infra/REQUESTS.md REQ-002) —
    and the released row keeps an audit trail. `mark_handled` re-claims a released row
    atomically via ON CONFLICT .. DO UPDATE .. WHERE released_at IS NOT NULL.
    """

    def is_handled(self, event_id: str) -> bool:
        # A released (tombstoned) row does NOT count as handled — the event is retryable.
        with self._tx() as cur:
            cur.execute(
                "SELECT 1 FROM stripe_events WHERE event_id = %s AND released_at IS NULL",
                (str(event_id),),
            )
            return cur.fetchone() is not None

    def mark_handled(self, event_id: str, account_id: str | None = None) -> bool:
        # The atomic claim: insert a fresh row, OR re-claim one a failed attempt released.
        # rowcount == 1 iff this call inserted or re-claimed; 0 = actively claimed elsewhere.
        # (The DO UPDATE's WHERE makes the held-claim case touch no row at all.)
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO stripe_events (event_id, account_id) VALUES (%s,%s) "
                "ON CONFLICT (event_id) DO UPDATE "
                "SET released_at = NULL, account_id = EXCLUDED.account_id, handled_at = now() "
                "WHERE stripe_events.released_at IS NOT NULL",
                (str(event_id), account_id),
            )
            return cur.rowcount == 1   # True = we claimed it; False = someone already had

    def release(self, event_id: str) -> None:
        # Tombstone, not delete (docstring): the claim is given back; the row stays for audit.
        with self._tx() as cur:
            cur.execute(
                "UPDATE stripe_events SET released_at = now() WHERE event_id = %s",
                (str(event_id),),
            )


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


class PgUsedTokenStore(_PgBase):
    """`signup.tokens.UsedTokenStore` over accounts.meta jsonb — single-use email-token nonces
    SHARED across Fargate tasks.

    WHY (security): `EmailTokenService` defaults to the per-process `InMemoryUsedTokenStore`,
    so with 2+ API tasks a consumed verification token verifies AGAIN on whichever task never
    saw it — a replay window as long as the token's 15-min TTL. Like PgOtpStore, the shared
    state rides the account row itself (NO new table): the token is account-bound (the HMAC'd
    body carries the account_id, proven before the single-use check runs), so the account row
    is the natural scope for its used-set.

    meta layout: `'used_email_tokens': {<nonce>: <expires_at epoch int>}` — written ONLY here.
    `mark_used` is ONE atomic statement that BOTH prunes expired nonces and adds the new one,
    with the merge computed inside Postgres under the row lock — no read-modify-write window
    for a concurrent account update (which itself merges, never replaces) or a concurrent
    mark_used to clobber. Pruning on write bounds growth: a nonce past its expiry can never
    verify again (the TTL check fails before the single-use check), so dropping it is safe and
    the map never holds more than the nonces consumed within one 15-minute TTL window.
    """

    def __init__(self, dsn: str, *, now=time.time):
        super().__init__(dsn)
        self._now = now

    def is_used(self, account_id: str, nonce: str) -> bool:
        # jsonb key-existence (`?`) — membership only. An expired-but-unpruned nonce reading
        # True is harmless: EmailTokenService rejects on TTL before it ever asks. No row
        # (unknown account) is an honest False — verify's account binding already failed it.
        with self._tx() as cur:
            cur.execute(
                "SELECT (meta->'used_email_tokens') ? %s AS used FROM accounts WHERE id = %s",
                (str(nonce), str(account_id)),
            )
            row = cur.fetchone()
        return bool(row and row.get("used"))

    def mark_used(self, account_id: str, nonce: str, expires_at: int) -> None:
        # ONE statement: rebuild the nonce map from the row's CURRENT value (under the UPDATE's
        # row lock) keeping only unexpired entries, then add this nonce — prune-on-write per the
        # class docstring. The other meta writers' top-level keys survive (merge, not replace).
        with self._tx() as cur:
            cur.execute(
                "UPDATE accounts SET meta = meta || jsonb_build_object("
                "'used_email_tokens', "
                "(SELECT COALESCE(jsonb_object_agg(t.key, t.value), '{}'::jsonb) "
                "FROM jsonb_each(COALESCE(accounts.meta->'used_email_tokens', '{}'::jsonb)) AS t "
                "WHERE (t.value)::text::bigint >= %s) "
                "|| jsonb_build_object(%s::text, %s::bigint)"
                "), updated_at = now() WHERE id = %s",
                (int(self._now()), str(nonce), int(expires_at), str(account_id)),
            )
