"""Public, unauthenticated support surface — POST /public/support (contact/help path).

The in-app Help / Contact-support form needs a real sink that works BEFORE any account
or tenant exists (a confused trial user, a prospect with a question, an existing customer
locked out of their session). PRE-TENANT and unauthenticated by design, so the defenses are
local and mirror POST /public/leads (api/public_routes.py) exactly:
  * STRICT validation — name/email/subject/message required, email shape-checked, an optional
    free-text tenant hint, unknown fields rejected (pydantic ``extra="forbid"``);
  * a 2KB raw-body CAP enforced BEFORE parsing (413) — a support note is roomier than a lead but
    is still not a place to paste a log file;
  * an in-process per-IP rate limit (fixed window, default 5/min — SUPPORT_RATE_PER_MINUTE)
    answering 429. In-process is honest scope: with 2 Fargate tasks the effective ceiling is
    N×limit; the CloudFront WAF rate rule remains the real flood gate.

The store is injected (PgSupportStore under the prod wiring; MemorySupportStore in tests).
``store=None`` mounts the route in its honest-503 "not configured" posture — the same
inert-default contract the other optional route groups follow.

THE TRUST RULE: the ``tenant`` field here is a FREE-TEXT HINT a confused user types ("I think my
workspace is acme"), stored only to help a human triage. It is NEVER trusted for authorization,
never used to bind RLS, never resolved to a real tenant_id. This route is pre-tenant; nothing it
writes is tenant-scoped.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError

# Reuse the signup plane's cheap server-side email shape guard (one definition, no drift).
from signup.accounts import _EMAIL_RE

# Reuse the leads route's IP-trust + in-process rate-limit machinery — one definition, no drift.
# (A support flood and a lead flood share the same trust boundary and the same honest in-process
# scope; duplicating the limiter would be a second thing to keep in sync.)
from api.public_routes import DEFAULT_TRUSTED_HOPS, _IpRateLimiter, _trusted_client_ip

MAX_BODY_BYTES = 2048          # the 2KB cap — enforced on the RAW body, before any parse
DEFAULT_RATE_PER_MINUTE = 5

# Per-field caps (chars). subject/message are roomier than a lead's; the 2KB raw cap is the
# outer bound — these keep any single field from eating the whole budget.
_FIELD_LIMITS = {"name": 200, "email": 320, "subject": 200, "message": 1400, "tenant": 200}

ENV_SUPPORT_RATE_PER_MINUTE = "SUPPORT_RATE_PER_MINUTE"
ENV_SUPPORT_TRUSTED_HOPS = "SUPPORT_TRUSTED_HOPS"


class SupportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")   # unknown fields are a 422, not silently dropped

    name: str
    email: str
    subject: str
    message: str
    # Optional free-text workspace HINT (see module docstring — never trusted for auth/RLS).
    tenant: str | None = None


class PgSupportStore:
    """Aurora-backed support sink (as the non-owner crm_app role).

    PRE-TENANT by nature (a support request precedes — or outlives — any tenant binding), so like
    accounts/leads the table is RLS-EXEMPT (db/schema.sql comment) and the store issues NO
    ``SET LOCAL app.current_tenant``. Connection discipline rides signup/store_pg.py's shared
    ``_PgBase`` (non-owner crm_app role, pooled per-op conns, one transaction per op). Import-safe:
    psycopg2 is imported lazily inside ``_PgBase.__init__``.
    """

    def __init__(self, dsn: str):
        from signup.store_pg import _PgBase  # noqa: PLC0415 — lazy; no driver at import
        self._base = _PgBase(dsn)

    def insert(self, *, name: str, email: str, subject: str, message: str,
               tenant: str | None = None, source_ip: str | None = None) -> str:
        request_id = str(uuid.uuid4())
        with self._base._tx() as cur:
            cur.execute(
                "INSERT INTO support_requests "
                "(id, name, email, subject, message, tenant_hint, source_ip) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (request_id, name, email, subject, message, tenant, source_ip),
            )
        return request_id


class MemorySupportStore:
    """In-memory stand-in (tests / local dev) — same insert contract."""

    def __init__(self):
        self.rows: list[dict] = []

    def insert(self, *, name: str, email: str, subject: str, message: str,
               tenant: str | None = None, source_ip: str | None = None) -> str:
        request_id = str(uuid.uuid4())
        self.rows.append({"id": request_id, "name": name, "email": email, "subject": subject,
                          "message": message, "tenant": tenant, "source_ip": source_ip})
        return request_id


@dataclass
class SupportDeps:
    # PgSupportStore / MemorySupportStore. None = the honest-503 unconfigured posture.
    support_store: Any | None = None
    rate_per_minute: int = DEFAULT_RATE_PER_MINUTE
    # Trusted proxy hops in front of this service (X-Forwarded-For parse — see api.public_routes
    # _trusted_client_ip). Default 2 = CloudFront -> ALB; the rate limit keys on the viewer IP at
    # this trust boundary, NOT the ALB socket peer (which is shared across every viewer).
    trusted_hops: int = DEFAULT_TRUSTED_HOPS
    _limiter: _IpRateLimiter | None = field(default=None, repr=False)

    def limiter(self) -> _IpRateLimiter:
        if self._limiter is None:
            self._limiter = _IpRateLimiter(self.rate_per_minute)
        return self._limiter


def build_support_deps() -> SupportDeps:
    """Env-built default (ApiDeps default_factory — api/asgi.py needs no change).

    The real Pg store rides the SAME deliberate gates as the rest of the public/signup plane:
    the SIGNUP_REAL_DEPS master switch AND a configured crm_app DSN (deploy invariance — DB_* env
    already rides the live task). Anything else = store None -> honest 503.
    """
    import os  # noqa: PLC0415

    from shared.config import dsn_from_env, load  # noqa: PLC0415

    cfg = load()
    try:
        rate = int(os.environ.get(ENV_SUPPORT_RATE_PER_MINUTE, DEFAULT_RATE_PER_MINUTE))
    except (TypeError, ValueError):
        rate = DEFAULT_RATE_PER_MINUTE
    try:
        hops = int(os.environ.get(ENV_SUPPORT_TRUSTED_HOPS, DEFAULT_TRUSTED_HOPS))
    except (TypeError, ValueError):
        hops = DEFAULT_TRUSTED_HOPS
    if hops < 1:
        hops = DEFAULT_TRUSTED_HOPS
    store = None
    if cfg.signup_real_deps:
        dsn = dsn_from_env()
        if dsn:
            store = PgSupportStore(dsn)
    return SupportDeps(support_store=store, rate_per_minute=rate, trusted_hops=hops)


def mount_support(app: FastAPI, deps: SupportDeps) -> None:
    @app.post("/public/support", status_code=201)
    async def create_support_request(request: Request):
        # 2KB cap FIRST, on the raw bytes — before any JSON parse or validation work.
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="support payload exceeds 2KB")
        # In-process per-IP rate limit, keyed on the VIEWER IP at the trust boundary
        # (X-Forwarded-For parsed `trusted_hops` from the right), NOT the ALB socket peer —
        # otherwise one attacker drains the shared quota for every viewer.
        viewer_ip = _trusted_client_ip(request, deps.trusted_hops)
        if not deps.limiter().allow(viewer_ip):
            raise HTTPException(status_code=429, detail="too many support requests from this address")
        try:
            body = SupportBody.model_validate_json(raw)
        except ValidationError as e:
            # include_input=False: raw (possibly junk binary) input must not ride the response
            # (it also isn't JSON-serializable when bytes — a 422 must never become a 500).
            raise HTTPException(
                status_code=422,
                detail=e.errors(include_url=False, include_input=False),
            )
        name = body.name.strip()
        email = body.email.strip().lower()
        subject = body.subject.strip()
        message = body.message.strip()
        if not name:
            raise HTTPException(status_code=422, detail="name must not be empty")
        if not subject:
            raise HTTPException(status_code=422, detail="subject must not be empty")
        if not message:
            raise HTTPException(status_code=422, detail="message must not be empty")
        if not _EMAIL_RE.match(email):
            raise HTTPException(status_code=422, detail="invalid email address")
        # Per-field caps (defense in depth behind the 2KB raw cap).
        for fld, cap in _FIELD_LIMITS.items():
            value = getattr(body, fld, None)
            if value is not None and len(value) > cap:
                raise HTTPException(status_code=422, detail=f"{fld} exceeds {cap} characters")
        if deps.support_store is None:
            # Honest unconfigured posture — never a fake success that drops the request.
            raise HTTPException(status_code=503, detail="support intake not configured")
        # Strip control chars from free-text (defense-in-depth; it's data, never executed).
        clean = lambda s: re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s) if s else s  # noqa: E731
        tenant_hint = clean(body.tenant.strip()) if body.tenant and body.tenant.strip() else None
        request_id = deps.support_store.insert(
            name=clean(name),
            email=email,
            subject=clean(subject),
            message=clean(message),
            tenant=tenant_hint,
            source_ip=viewer_ip,   # the trust-boundary viewer IP (same value the limiter keyed on)
        )
        return {"ok": True, "id": request_id}
