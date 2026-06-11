"""Greenlight — the human-in-the-loop approval queue (Build Guide Phase 5, Step 30).

When a side-effecting action needs confirmation, persist it to `approvals` and surface it: every item
carries the agent's reasoning, an editable draft, and the value at stake. A human approves / edits /
denies. Maps to the Managed Agents tool-confirmation reply (user.tool_confirmation allow/deny) — that
mapping is authored + flagged "verify"; it is never called live here.

Conforms to the `Greenlight` protocol in agents/tools/base.py (so Phase 4 tools route through it).
"""
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Protocol

# Approval expiry (customer-readiness audit P0): propose() stamps expires_at = now + TTL.
# Expiry is LAZY — an expired row drops out of the pending list/count and a decide() on it flips
# it to status='expired' and raises; no sweeper is required for safety. ttl_hours <= 0 disables
# stamping (legacy rows with NULL expires_at never expire).
ENV_APPROVAL_TTL_HOURS = "GREENLIGHT_TTL_HOURS"
DEFAULT_APPROVAL_TTL_HOURS = 168.0  # 7 days

# GET /approvals pagination bounds (mirrors the traces keyset pattern).
DEFAULT_APPROVALS_LIMIT = 100
MAX_APPROVALS_LIMIT = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(rec: dict) -> bool:
    exp = rec.get("expires_at")
    return exp is not None and exp <= _now()


def _ttl_hours(explicit: float | None) -> float:
    if explicit is not None:
        return float(explicit)
    raw = os.environ.get(ENV_APPROVAL_TTL_HOURS, "")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_APPROVAL_TTL_HOURS


class EditNotAllowed(ValueError):
    """An edit-approve tried to change a key outside the proposal's editable payload fields.

    The 'action' key (and any key not already present in the proposed payload) is never editable —
    a human edit may tune WHAT the approved action does, never swap it for a different action.
    Maps to 422 at the API boundary (a subclass of ValueError so untyped callers still fail safe).
    """


class ApprovalStore(Protocol):
    def insert(self, row: dict) -> object: ...
    def get(self, tenant_id: str, approval_id: object) -> dict | None: ...
    def list_pending(self, tenant_id: str) -> list[dict]: ...
    def page_pending(self, tenant_id: str, *, limit: int,
                     cursor: str | None = None) -> tuple[list[dict], str | None]: ...
    def count_pending(self, tenant_id: str) -> int: ...
    def update(self, tenant_id: str, approval_id: object, changes: dict,
               *, expected_status: str | None = None) -> int: ...


class InMemoryApprovalStore:
    """Offline approval store (the real one is `PgApprovalStore` over Aurora with RLS)."""

    def __init__(self):
        self._rows: dict[int, dict] = {}
        self._n = 0
        self._lock = threading.Lock()

    @staticmethod
    def _key(approval_id):
        # tolerate numeric string ids (FastAPI path params arrive as strings).
        s = str(approval_id)
        return int(s) if s.isdigit() else s

    def insert(self, row: dict) -> int:
        with self._lock:
            self._n += 1
            row = {"id": self._n, "applied_at": None, "apply_result": None,
                   "created_at": _now(), **row}
            self._rows[self._n] = row
            return self._n

    def get(self, tenant_id: str, approval_id) -> dict | None:
        row = self._rows.get(self._key(approval_id))
        # Tenant-scope the read (mirrors the Pg RLS boundary): never return another tenant's row.
        if row is None or str(row["tenant_id"]) != str(tenant_id):
            return None
        return row

    def _pending(self, tenant_id: str) -> list[dict]:
        # Insertion (id) order; expired rows are invisible (lazy expiry — see module constants).
        return [r for r in self._rows.values()
                if str(r["tenant_id"]) == str(tenant_id) and r["status"] == "pending"
                and not _is_expired(r)]

    def list_pending(self, tenant_id: str) -> list[dict]:
        return self._pending(tenant_id)

    def page_pending(self, tenant_id: str, *, limit: int,
                     cursor: str | None = None) -> tuple[list[dict], str | None]:
        """Keyset page over the pending queue in insertion (id) order. The cursor is the last
        returned row's id; an unparseable cursor raises ValueError (callers map it to 422)."""
        after = 0
        if cursor is not None:
            try:
                after = int(cursor)
            except (TypeError, ValueError):
                raise ValueError(f"invalid cursor {cursor!r}")
        rows = [r for r in self._pending(tenant_id) if int(r["id"]) > after]
        page = rows[:limit]
        next_cursor = str(page[-1]["id"]) if len(rows) > limit else None
        return page, next_cursor

    def count_pending(self, tenant_id: str) -> int:
        return len(self._pending(tenant_id))

    def update(self, tenant_id: str, approval_id, changes: dict,
               *, expected_status: str | None = None) -> int:
        """Tenant-scoped update; returns the touched-row count (0 or 1), mirroring Pg's rowcount.

        With `expected_status`, the write is an atomic CHECK-AND-SET under the store lock: the row
        is mutated only if its CURRENT status equals `expected_status`, so two concurrent deciders
        racing pending->decided can never both win (the loser gets 0 — same contract as the
        conditional `UPDATE ... AND status = %s` in PgApprovalStore).
        """
        with self._lock:
            row = self._rows.get(self._key(approval_id))
            if row is None or str(row["tenant_id"]) != str(tenant_id):
                return 0  # tenant-scoped: silently ignore a cross-tenant write
            if expected_status is not None and row["status"] != expected_status:
                return 0  # lost the race — another decider already moved the row on
            row.update(changes)
            return 1


class PgApprovalStore:
    """Aurora-backed approval store over the `approvals` table.

    Connects as the non-owner crm_app role. Each operation checks out a connection from a thread-safe
    pool and runs in ONE transaction that begins with `SET LOCAL app.current_tenant = %s` (the tenant
    for THIS operation) — so Postgres RLS scopes every read/write and the GUC auto-resets at txn end,
    never leaking past the unit of work across the pooled connection. Import-safe (psycopg2 imported
    lazily on construction). Ids are the table's uuids (as strings).
    """

    def __init__(self, dsn: str):
        import psycopg2  # noqa: PLC0415 — guarded
        import psycopg2.pool  # noqa: PLC0415
        from psycopg2.extras import Json, RealDictCursor  # noqa: PLC0415
        self._psycopg2 = psycopg2
        self._Json = Json
        self._cursor_factory = RealDictCursor
        pool_max = int(os.environ.get("UPLIFT_DB_POOL_MAX", "10"))
        # min == max: a fixed-size pool RETAINS returned connections (psycopg2 closes any
        # connection beyond minconn on putconn), avoiding TCP/auth churn under concurrent load.
        self._pool = psycopg2.pool.ThreadedConnectionPool(1, pool_max, dsn)

    @staticmethod
    def _row(row) -> dict | None:
        if row is None:
            return None
        out = dict(row)
        out.setdefault("applied_at", None)
        out.setdefault("apply_result", None)
        return out

    def _getconn(self):
        """Check out a pooled connection, waiting briefly if the pool is momentarily exhausted.

        psycopg2's pool raises rather than blocks when all connections are out; under a burst wider
        than the pool (the anyio threadpool can exceed pool_max) we'd otherwise 500. Wait up to a few
        seconds for a peer's short tenant-scoped txn to release one, then give up.
        """
        import time  # noqa: PLC0415
        deadline = time.monotonic() + 10.0
        while True:
            try:
                return self._pool.getconn()
            except self._psycopg2.pool.PoolError as exc:
                if "exhausted" not in str(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.005)

    @contextmanager
    def _tx(self, tenant_id):
        """Yield a RealDict cursor inside a single tenant-scoped transaction.

        Begins with `SET LOCAL app.current_tenant` (auto-resets at COMMIT/ROLLBACK), commits on
        success / rolls back on error, and always returns the connection to the pool. The per-op
        connection is never shared across threads (checked out for the duration of the txn).
        """
        conn = self._getconn()
        try:
            cur = conn.cursor(cursor_factory=self._cursor_factory)
            cur.execute("SET LOCAL app.current_tenant = %s", (str(tenant_id),))
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    def insert(self, row: dict) -> str:
        with self._tx(row["tenant_id"]) as cur:
            cur.execute(
                "INSERT INTO approvals (tenant_id, proposed_action, agent, reasoning, value_at_stake, status, expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (row["tenant_id"], self._Json(row["proposed_action"]), row.get("agent"),
                 row.get("reasoning"), row.get("value_at_stake"), row.get("status", "pending"),
                 row.get("expires_at")),
            )
            return str(cur.fetchone()["id"])

    def get(self, tenant_id: str, approval_id) -> dict | None:
        with self._tx(tenant_id) as cur:
            cur.execute("SELECT * FROM approvals WHERE id = %s", (str(approval_id),))
            row = cur.fetchone()
        return self._row(row)

    # Lazy expiry: an expired row simply stops being "pending" to readers (decide() flips it).
    _PENDING_WHERE = "status = 'pending' AND (expires_at IS NULL OR expires_at > now())"

    def list_pending(self, tenant_id: str) -> list[dict]:
        with self._tx(tenant_id) as cur:
            cur.execute(f"SELECT * FROM approvals WHERE {self._PENDING_WHERE} ORDER BY created_at, id")
            return [self._row(r) for r in cur.fetchall()]

    @staticmethod
    def _parse_cursor(cursor: str) -> tuple[datetime, str]:
        """Validate + split an opaque '<created_at iso>|<uuid>' keyset cursor (422 on junk)."""
        try:
            ts_raw, _, id_raw = cursor.partition("|")
            return datetime.fromisoformat(ts_raw), str(uuid.UUID(id_raw))
        except (TypeError, ValueError, AttributeError):
            raise ValueError(f"invalid cursor {cursor!r}")

    def page_pending(self, tenant_id: str, *, limit: int,
                     cursor: str | None = None) -> tuple[list[dict], str | None]:
        """Keyset page over the pending queue, (created_at, id) ascending — the same opaque-cursor
        contract as PgTraceStore. Served by the partial approvals_tenant_pending_idx."""
        params: list = []
        where = self._PENDING_WHERE
        if cursor is not None:
            ts, row_id = self._parse_cursor(cursor)
            where += " AND (created_at, id) > (%s, %s)"
            params.extend([ts, row_id])
        params.append(limit + 1)  # one extra row decides whether a next page exists
        with self._tx(tenant_id) as cur:
            cur.execute(
                f"SELECT * FROM approvals WHERE {where} ORDER BY created_at, id LIMIT %s",
                params,
            )
            rows = [self._row(r) for r in cur.fetchall()]
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = f"{last['created_at'].isoformat()}|{last['id']}"
        return page, next_cursor

    def count_pending(self, tenant_id: str) -> int:
        with self._tx(tenant_id) as cur:
            cur.execute(f"SELECT count(*) AS n FROM approvals WHERE {self._PENDING_WHERE}")
            return int(cur.fetchone()["n"])

    def update(self, tenant_id: str, approval_id, changes: dict,
               *, expected_status: str | None = None) -> int:
        """Tenant-scoped UPDATE; returns the rowcount.

        With `expected_status`, the UPDATE is CONDITIONAL — `... WHERE id = %s AND status = %s` —
        so Postgres's row lock arbitrates concurrent deciders atomically: exactly one transition
        wins (rowcount 1) and every racer loses honestly (rowcount 0). The per-op
        `SET LOCAL app.current_tenant` transaction pattern is unchanged (RLS still scopes the write).
        """
        if not changes:
            return 0
        cols = ", ".join(f"{k} = %s" for k in changes)
        # jsonb columns (e.g. proposed_action) need the Json adapter.
        vals = [self._Json(v) if isinstance(v, dict) else v for v in changes.values()]
        vals.append(str(approval_id))
        sql = f"UPDATE approvals SET {cols} WHERE id = %s"
        if expected_status is not None:
            sql += " AND status = %s"
            vals.append(expected_status)
        with self._tx(tenant_id) as cur:
            cur.execute(sql, vals)
            return cur.rowcount


class Greenlight:
    def __init__(self, store: ApprovalStore | None = None, *, ttl_hours: float | None = None):
        self.store = store or InMemoryApprovalStore()
        # None -> GREENLIGHT_TTL_HOURS env -> 7-day default; <= 0 disables expiry stamping.
        self.ttl_hours = _ttl_hours(ttl_hours)

    # --- matches agents.tools.base.Greenlight.propose(...) ---
    def propose(self, *, tenant_id: str, action: str, agent: str | None,
                reasoning: str, value_at_stake: float | None, payload: dict) -> dict:
        # The registry-derived `action` is the discriminator the applier dispatches on
        # and the label compliance/traces key off. A client-supplied payload['action']
        # must never override it (audit-label divergence + a latent compliance
        # route-around) — the spread order below makes the trusted name win.
        expires_at = _now() + timedelta(hours=self.ttl_hours) if self.ttl_hours > 0 else None
        approval_id = self.store.insert({
            "tenant_id": tenant_id,
            "proposed_action": {**payload, "action": action},
            "agent": agent,
            "reasoning": reasoning,
            "value_at_stake": value_at_stake,
            "status": "pending",
            "expires_at": expires_at,
        })
        return self.store.get(tenant_id, approval_id)

    def list_pending(self, tenant_id: str) -> list[dict]:
        return self.store.list_pending(tenant_id)

    def page_pending(self, tenant_id: str, *, limit: int = DEFAULT_APPROVALS_LIMIT,
                     cursor: str | None = None) -> tuple[list[dict], str | None]:
        """One bounded page of the pending queue + the opaque cursor for the next page."""
        n = max(1, min(int(limit), MAX_APPROVALS_LIMIT))
        return self.store.page_pending(tenant_id, limit=n, cursor=cursor)

    def count_pending(self, tenant_id: str) -> int:
        return self.store.count_pending(tenant_id)

    @staticmethod
    def _not_pending(approval_id, rec: dict | None) -> ValueError:
        """The honest already-decided error: name the actual status when we can see it."""
        if rec is not None and rec.get("status") not in (None, "pending"):
            return ValueError(f"approval {approval_id} already {rec['status']}")
        return ValueError(f"approval {approval_id} not pending")

    def decide(self, tenant_id: str, approval_id: int, decision: str, *, edits: dict | None = None,
               deny_message: str = "", decided_by: str | None = None) -> dict:
        """Apply a human decision. 'approve' | 'edit' (approve with edits) | 'deny'.

        tenant_id is the verified per-request tenant (THE TRUST RULE) — threaded into every store call
        so RLS scopes the read/write; the store never relies on shared connection state.

        The pending->decided transition is ATOMIC: the store's conditional update
        (`expected_status='pending'`) arbitrates concurrent deciders, so a TOCTOU race between the
        read above and the write below can never double-decide — the loser's rowcount is 0 and it
        raises exactly like an already-decided approval (the caller must never apply for the loser).
        """
        rec = self.store.get(tenant_id, approval_id)
        if rec is None or rec["status"] != "pending":
            raise self._not_pending(approval_id, rec)
        if _is_expired(rec):
            # Lazy expiry: flip the row (conditionally — a racing decider may have moved it) and
            # refuse the decision. An expired draft is stale context; it must never fire late.
            self.store.update(tenant_id, approval_id, {"status": "expired"},
                              expected_status="pending")
            raise ValueError(f"approval {approval_id} expired")
        if decision == "deny":
            changes = {"status": "denied", "deny_message": deny_message, "decided_by": decided_by}
        elif decision in ("approve", "edit"):
            action = dict(rec["proposed_action"])
            if decision == "edit" and edits:
                # Edit guard: a human edit may tune the proposal's PAYLOAD fields only — never the
                # 'action' key (no swapping send_email for create_deal) and never a novel key.
                editable = set(action) - {"action"}
                bad = sorted(k for k in edits if k not in editable)
                if bad:
                    raise EditNotAllowed(
                        "edit may only change the proposal's payload fields; "
                        f"not editable: {', '.join(map(repr, bad))}"
                    )
                action.update(edits)
            changes = {"status": "approved", "proposed_action": action, "decided_by": decided_by}
        else:
            raise ValueError(f"unknown decision {decision!r}")
        if self.store.update(tenant_id, approval_id, changes, expected_status="pending") == 0:
            # Lost the race (or the row was decided between read and write) — never double-decide.
            # Re-read so the loser's error names where the row actually landed.
            raise self._not_pending(approval_id, self.store.get(tenant_id, approval_id))
        return self.store.get(tenant_id, approval_id)

    def to_ma_confirmation(self, rec: dict, tool_use_id: str) -> dict:
        """The Managed Agents reply event for this decision (VERIFY against live SDK; not sent here)."""
        if rec["status"] == "approved":
            return {"type": "user.tool_confirmation", "tool_use_id": tool_use_id, "result": "allow",
                    "edited_input": rec["proposed_action"]}
        return {"type": "user.tool_confirmation", "tool_use_id": tool_use_id, "result": "deny",
                "deny_message": rec.get("deny_message", "")}
