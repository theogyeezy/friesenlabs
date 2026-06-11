"""Public, unauthenticated marketing routes — POST /public/leads (revenue lane).

The landing page's "book a call" / "email us" forms need a real sink. PRE-TENANT and
unauthenticated by design (a lead precedes any account), so the defenses are local:
  * STRICT validation — kind is a closed enum, name/email required, email shape-checked,
    unknown fields rejected (pydantic ``extra="forbid"``);
  * a 1KB raw-body CAP enforced BEFORE parsing (413) — nobody stores a novel in `message`;
  * an in-process per-IP rate limit (fixed window, default 5/min — PUBLIC_LEADS_RATE_PER_MINUTE,
    shared/config.py) answering 429. In-process is honest scope: with 2 Fargate tasks the
    effective ceiling is N×limit; the CloudFront WAF rate rule remains the real flood gate.

The store is injected (signup/leads.py PgLeadStore under the prod wiring; MemoryLeadStore in
tests). ``store=None`` mounts the route in its honest-503 "not configured" posture — the same
inert-default contract the other optional route groups follow.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError

# Reuse the signup plane's cheap server-side email shape guard (one definition, no drift).
from signup.accounts import _EMAIL_RE

MAX_BODY_BYTES = 1024          # the 1KB cap — enforced on the RAW body, before any parse
DEFAULT_RATE_PER_MINUTE = 5
_WINDOW_SECONDS = 60.0

_FIELD_LIMITS = {"name": 200, "email": 320, "message": 600, "company": 200}


class LeadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")   # unknown fields are a 422, not silently dropped

    kind: Literal["book_call", "email"]
    name: str
    email: str
    message: str | None = None
    company: str | None = None


class _IpRateLimiter:
    """Fixed-window in-process counter per IP. Deliberately simple (module docstring)."""

    def __init__(self, limit: int, now: Callable[[], float] = time.time):
        self.limit = max(int(limit), 1)
        self.now = now
        self._hits: dict[str, list[float]] = {}

    def allow(self, ip: str) -> bool:
        cutoff = self.now() - _WINDOW_SECONDS
        hits = [t for t in self._hits.get(ip, []) if t > cutoff]
        if len(hits) >= self.limit:
            self._hits[ip] = hits
            return False
        hits.append(self.now())
        self._hits[ip] = hits
        if len(self._hits) > 10_000:   # bounded memory: drop idle IPs wholesale
            self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > cutoff}
        return True


@dataclass
class PublicDeps:
    # signup.leads.PgLeadStore / MemoryLeadStore. None = the honest-503 unconfigured posture.
    leads_store: Any | None = None
    rate_per_minute: int = DEFAULT_RATE_PER_MINUTE
    now: Callable[[], float] = time.time
    _limiter: _IpRateLimiter | None = field(default=None, repr=False)

    def limiter(self) -> _IpRateLimiter:
        if self._limiter is None:
            self._limiter = _IpRateLimiter(self.rate_per_minute, self.now)
        return self._limiter


def build_public_deps() -> PublicDeps:
    """Env-built default (ApiDeps default_factory — api/asgi.py needs no change).

    The real Pg store rides the SAME deliberate gates as the rest of the signup plane: the
    SIGNUP_REAL_DEPS master switch AND a configured crm_app DSN (deploy invariance — DB_* env
    already rides the live task for other features). Anything else = store None -> honest 503.
    """
    import os  # noqa: PLC0415

    from shared.config import ENV_PUBLIC_LEADS_RATE_PER_MINUTE, dsn_from_env, load  # noqa: PLC0415

    cfg = load()
    try:
        rate = int(os.environ.get(ENV_PUBLIC_LEADS_RATE_PER_MINUTE, DEFAULT_RATE_PER_MINUTE))
    except (TypeError, ValueError):
        rate = DEFAULT_RATE_PER_MINUTE
    store = None
    if cfg.signup_real_deps:
        dsn = dsn_from_env()
        if dsn:
            from signup.leads import PgLeadStore  # noqa: PLC0415 — lazy; no driver at import
            store = PgLeadStore(dsn)
    return PublicDeps(leads_store=store, rate_per_minute=rate)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def mount_public(app: FastAPI, deps: PublicDeps) -> None:
    @app.post("/public/leads", status_code=201)
    async def create_lead(request: Request):
        # 1KB cap FIRST, on the raw bytes — before any JSON parse or validation work.
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="lead payload exceeds 1KB")
        # In-process per-IP rate limit (honest scope: per task — see module docstring).
        if not deps.limiter().allow(_client_ip(request)):
            raise HTTPException(status_code=429, detail="too many leads from this address")
        try:
            body = LeadBody.model_validate_json(raw)
        except ValidationError as e:
            # include_input=False: raw (possibly junk binary) input must not ride the response
            # (it also isn't JSON-serializable when bytes — a 422 must never become a 500).
            raise HTTPException(
                status_code=422,
                detail=e.errors(include_url=False, include_input=False),
            )
        name = body.name.strip()
        email = body.email.strip().lower()
        if not name:
            raise HTTPException(status_code=422, detail="name must not be empty")
        if not _EMAIL_RE.match(email):
            raise HTTPException(status_code=422, detail="invalid email address")
        for fld, cap in _FIELD_LIMITS.items():
            value = getattr(body, fld, None)
            if value is not None and len(value) > cap:
                raise HTTPException(status_code=422, detail=f"{fld} exceeds {cap} characters")
        if deps.leads_store is None:
            # Honest unconfigured posture — never a fake success that drops the lead.
            raise HTTPException(status_code=503, detail="lead capture not configured")
        # Strip control chars from free-text (defense-in-depth; it's data, never executed).
        clean = lambda s: re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s) if s else s  # noqa: E731
        lead_id = deps.leads_store.insert(
            kind=body.kind,
            name=clean(name),
            email=email,
            message=clean(body.message.strip()) if body.message else None,
            company=clean(body.company.strip()) if body.company else None,
            source_ip=_client_ip(request),
        )
        return {"ok": True, "id": lead_id}
