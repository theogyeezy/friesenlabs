"""Sell (gamification) data-foundation stores — the roster (`members`) + the append-only points
trail (`points_ledger`), with offline in-memory fakes for tests.

WHAT THIS BACKS: the leaderboard / per-user-summary surface. Points are stored as an APPEND-ONLY
ledger (one immutable row per scored event); the leaderboard + summary are SUM aggregates over it,
never a mutable running total — so a mis-scored event is corrected by appending a compensating row,
never by editing history.

RLS DISCIPLINE: every Pg op REUSES the `_PgTenantClient` plumbing from api/pg_clients.py (imported,
not re-implemented) — a pooled per-op connection in ONE transaction that begins with
`SET LOCAL app.current_tenant` (auto-resets at txn end), connecting as the non-owner `crm_app` role.
Postgres RLS scopes every read/write; there is NO hand-written `WHERE tenant_id = ...` for tenancy
on the real stores. `members` and `points_ledger` are FORCE'd RLS tenant tables (db/schema.sql);
crm_app gets full DML on members and SELECT/INSERT-only on points_ledger (db/roles.sql).

THE TRUST RULE: `tenant_id` and `user_id` flow in from the caller (the verified Cognito JWT claim +
subject threaded by the route) — never from env, headers, or request bodies here.

Import-safe: psycopg2 is imported lazily by `_PgTenantClient` (DSN path only), so importing this
module needs no network, AWS, or psycopg2 — the in-memory fakes are pure Python.
"""
from __future__ import annotations

from typing import Any

from api.pg_clients import _as_iso, _as_str, _dict_one, _dict_rows, _PgTenantClient


# --------------------------------------------------------------------------- #
# Row normalizers — one wire shape regardless of cursor flavor (psycopg2 / fake).
# --------------------------------------------------------------------------- #
def _member_out(row: dict) -> dict:
    """One members row in the wire shape."""
    return {
        "tenant_id": _as_str(row.get("tenant_id")),
        "user_id": _as_str(row.get("user_id")),
        "display_name": row.get("display_name"),
        "role": row.get("role"),
        "first_seen": _as_iso(row.get("first_seen")),
        "last_seen": _as_iso(row.get("last_seen")),
    }


def _leaderboard_out(row: dict) -> dict:
    """One leaderboard row: a member's total points + event count over the window."""
    return {
        "user_id": _as_str(row.get("user_id")),
        "display_name": row.get("display_name"),
        "points": int(row.get("points") or 0),
        "events": int(row.get("events") or 0),
    }


def _event_out(row: dict) -> dict:
    """One scored ledger event in the wire shape (the /sell/me streak + today-progress source)."""
    return {
        "user_id": _as_str(row.get("user_id")),
        "event_type": row.get("event_type"),
        "points": int(row.get("points") or 0),
        "occurred_at": _as_iso(row.get("occurred_at")),
    }


# --------------------------------------------------------------------------- #
# Members
# --------------------------------------------------------------------------- #
class PgMemberStore(_PgTenantClient):
    """Aurora-backed roster over `members`, RLS-scoped via SET LOCAL (connects as crm_app).

    Construct with EITHER a `dsn` (a pool is built; psycopg2 imported lazily) OR a `conn_factory`
    (zero-arg callable -> a DB-API connection per op) — exactly like every store in pg_clients.py.
    """

    def upsert(self, tenant_id, user_id: str, display_name: str | None = None,
               role: str | None = None) -> dict:
        """Upsert a member and RETURN the saved row, refreshing `last_seen` to now().

        ON CONFLICT (tenant_id, user_id) DO UPDATE keeps the original `first_seen`, bumps
        `last_seen`, and updates display_name/role ONLY when a non-None value is provided
        (COALESCE leaves the stored value untouched otherwise — a bare presence-ping never
        erases a known name).
        """
        with self._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO members (tenant_id, user_id, display_name, role, last_seen) "
                "VALUES (%s, %s, %s, %s, now()) "
                "ON CONFLICT (tenant_id, user_id) DO UPDATE SET "
                "display_name = COALESCE(EXCLUDED.display_name, members.display_name), "
                "role = COALESCE(EXCLUDED.role, members.role), "
                "last_seen = now() "
                "RETURNING tenant_id, user_id, display_name, role, first_seen, last_seen",
                (str(tenant_id), user_id, display_name, role),
            )
            row = _dict_one(cur)
        if row is None:
            raise RuntimeError("member upsert returned no row")
        return _member_out(row)

    def list(self, tenant_id) -> list[dict]:
        """The tenant's roster, most-recently-active first. RLS-scoped via SET LOCAL."""
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT tenant_id, user_id, display_name, role, first_seen, last_seen "
                "FROM members ORDER BY last_seen DESC",
            )
            rows = _dict_rows(cur)
        return [_member_out(r) for r in rows]


# --------------------------------------------------------------------------- #
# Points ledger (append-only)
# --------------------------------------------------------------------------- #
class PgPointsStore(_PgTenantClient):
    """Aurora-backed append-only points ledger over `points_ledger`, RLS-scoped via SET LOCAL.

    crm_app holds SELECT/INSERT only (db/roles.sql) — there is no update/delete path here by
    design; the ledger is immutable audit, like traces/cost_events.
    """

    def append(self, row: dict) -> None:
        """Append one scored event. `row`: {tenant_id, user_id, event_type, points, deal_id?,
        occurred_at?}. tenant_id is REQUIRED (it anchors the RLS policy). deal_id/occurred_at are
        optional (occurred_at defaults to now() in the column)."""
        tenant_id = row.get("tenant_id")
        if tenant_id is None:
            raise ValueError("points_ledger append requires tenant_id")
        with self._tx(tenant_id) as cur:
            cur.execute(
                "INSERT INTO points_ledger "
                "(tenant_id, user_id, event_type, points, deal_id, occurred_at) "
                "VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()))",
                (
                    str(tenant_id),
                    row.get("user_id"),
                    row.get("event_type"),
                    row.get("points"),
                    row.get("deal_id"),
                    row.get("occurred_at"),
                ),
            )

    def leaderboard(self, tenant_id, since=None) -> list[dict]:
        """Per-user total points + event count, highest first. RLS-scoped via SET LOCAL.

        `since` is an OPTIONAL inclusive lower bound on occurred_at (a timestamptz / ISO string);
        when None the whole history counts. display_name is LEFT-joined from members (None when a
        scoring user has no roster row yet)."""
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT p.user_id AS user_id, m.display_name AS display_name, "
                "       COALESCE(SUM(p.points), 0) AS points, COUNT(*) AS events "
                "FROM points_ledger p "
                "LEFT JOIN members m ON m.tenant_id = p.tenant_id AND m.user_id = p.user_id "
                "WHERE (%s::timestamptz IS NULL OR p.occurred_at >= %s::timestamptz) "
                "GROUP BY p.user_id, m.display_name "
                "ORDER BY points DESC, events DESC",
                (since, since),
            )
            rows = _dict_rows(cur)
        return [_leaderboard_out(r) for r in rows]

    def user_summary(self, tenant_id, user_id: str) -> dict:
        """One user's total points + event count over the whole history. RLS-scoped via SET LOCAL."""
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT COALESCE(SUM(points), 0) AS points, COUNT(*) AS events "
                "FROM points_ledger WHERE user_id = %s",
                (user_id,),
            )
            row = _dict_one(cur) or {}
        return {
            "user_id": user_id,
            "points": int(row.get("points") or 0),
            "events": int(row.get("events") or 0),
        }

    def user_events(self, tenant_id, user_id: str, since=None) -> list[dict]:
        """One user's scored events ({user_id, event_type, points, occurred_at}), newest first —
        what the /sell/me surface derives a streak + today-progress from. RLS-scoped via SET LOCAL.

        `since` is an OPTIONAL inclusive lower bound on occurred_at (a timestamptz / ISO string);
        when None the whole history is returned."""
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT user_id, event_type, points, occurred_at FROM points_ledger "
                "WHERE user_id = %s "
                "  AND (%s::timestamptz IS NULL OR occurred_at >= %s::timestamptz) "
                "ORDER BY occurred_at DESC",
                (user_id, since, since),
            )
            rows = _dict_rows(cur)
        return [_event_out(r) for r in rows]

    def leaderboard_rows(self, tenant_id, since=None) -> list[dict]:
        """Like `leaderboard`, but each row is STAMPED with its tenant_id (independently from the
        ledger) so an API route can run a defense-in-depth tenant re-check before stripping the
        internal id from the wire. RLS-scoped via SET LOCAL — the tenant_id can only ever be the
        scoped one, but the field makes a silent leak fail loud at the boundary, not propagate."""
        with self._tx(tenant_id) as cur:
            cur.execute(
                "SELECT p.tenant_id AS tenant_id, p.user_id AS user_id, "
                "       m.display_name AS display_name, "
                "       COALESCE(SUM(p.points), 0) AS points, COUNT(*) AS events "
                "FROM points_ledger p "
                "LEFT JOIN members m ON m.tenant_id = p.tenant_id AND m.user_id = p.user_id "
                "WHERE (%s::timestamptz IS NULL OR p.occurred_at >= %s::timestamptz) "
                "GROUP BY p.tenant_id, p.user_id, m.display_name "
                "ORDER BY points DESC, events DESC",
                (since, since),
            )
            rows = _dict_rows(cur)
        return [{"tenant_id": _as_str(r.get("tenant_id")), **_leaderboard_out(r)} for r in rows]


# --------------------------------------------------------------------------- #
# Offline in-memory fakes — the default offline path + the test doubles.
# Isolation here is PURE explicit tenant filtering (no DB, no RLS): every op is
# scoped by its tenant_id argument, so a caller can never read or mutate another
# tenant's row. (The cross-thread Pg/RLS proof lives in tests/integration.)
# --------------------------------------------------------------------------- #
class InMemoryMemberStore:
    """Offline roster (the real one is `PgMemberStore` over Aurora, tenant-scoped via RLS)."""

    def __init__(self):
        self.rows: dict[tuple[str, str], dict] = {}
        self._seq = 0

    def upsert(self, tenant_id, user_id: str, display_name: str | None = None,
               role: str | None = None) -> dict:
        key = (str(tenant_id), str(user_id))
        self._seq += 1
        existing = self.rows.get(key)
        if existing is None:
            row = {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "display_name": display_name,
                "role": role,
                "first_seen": "1970-01-01T00:00:00+00:00",
                # monotonically increasing stand-in for now() so list() ordering is stable.
                "last_seen": self._seq,
            }
        else:
            row = dict(existing)
            if display_name is not None:
                row["display_name"] = display_name
            if role is not None:
                row["role"] = role
            row["last_seen"] = self._seq
        self.rows[key] = row
        return _member_out(row)

    def list(self, tenant_id) -> list[dict]:
        scoped = [r for r in self.rows.values() if r["tenant_id"] == str(tenant_id)]
        scoped.sort(key=lambda r: r["last_seen"], reverse=True)
        return [_member_out(r) for r in scoped]


class InMemoryPointsStore:
    """Offline append-only points ledger (the real one is `PgPointsStore`, tenant-scoped via RLS)."""

    def __init__(self, members: InMemoryMemberStore | None = None):
        self.rows: list[dict] = []
        # Optional roster for the leaderboard display-name join (mirrors the LEFT JOIN).
        self._members = members

    def append(self, row: dict) -> None:
        tenant_id = row.get("tenant_id")
        if tenant_id is None:
            raise ValueError("points_ledger append requires tenant_id")
        self.rows.append({
            "tenant_id": str(tenant_id),
            "user_id": row.get("user_id"),
            "event_type": row.get("event_type"),
            "points": row.get("points"),
            "deal_id": row.get("deal_id"),
            "occurred_at": row.get("occurred_at"),
        })

    def _display_name(self, tenant_id: str, user_id) -> Any:
        if self._members is None:
            return None
        member = self._members.rows.get((str(tenant_id), str(user_id)))
        return member.get("display_name") if member else None

    def leaderboard(self, tenant_id, since=None) -> list[dict]:
        totals: dict[Any, dict] = {}
        for r in self.rows:
            if r["tenant_id"] != str(tenant_id):
                continue
            if since is not None and r.get("occurred_at") is not None \
                    and r["occurred_at"] < since:
                continue
            agg = totals.setdefault(r["user_id"], {"points": 0, "events": 0})
            agg["points"] += int(r.get("points") or 0)
            agg["events"] += 1
        out = [
            _leaderboard_out({
                "user_id": uid,
                "display_name": self._display_name(tenant_id, uid),
                "points": agg["points"],
                "events": agg["events"],
            })
            for uid, agg in totals.items()
        ]
        out.sort(key=lambda r: (r["points"], r["events"]), reverse=True)
        return out

    def user_summary(self, tenant_id, user_id: str) -> dict:
        points = 0
        events = 0
        for r in self.rows:
            if r["tenant_id"] != str(tenant_id) or str(r.get("user_id")) != str(user_id):
                continue
            points += int(r.get("points") or 0)
            events += 1
        return {"user_id": user_id, "points": points, "events": events}

    def user_events(self, tenant_id, user_id: str, since=None) -> list[dict]:
        scoped = []
        for r in self.rows:
            if r["tenant_id"] != str(tenant_id) or str(r.get("user_id")) != str(user_id):
                continue
            occurred_at = r.get("occurred_at")
            if since is not None and occurred_at is not None and occurred_at < since:
                continue
            scoped.append(_event_out(r))
        # Newest first; events with no occurred_at sort last (None -> "").
        scoped.sort(key=lambda e: e["occurred_at"] or "", reverse=True)
        return scoped

    def leaderboard_rows(self, tenant_id, since=None) -> list[dict]:
        return [{"tenant_id": str(tenant_id), **r}
                for r in self.leaderboard(tenant_id, since=since)]
