"""Cognito multi-tenant auth — THE TRUST RULE (Build Guide Phase 9, Step 48).

The API validates the JWT signature against the Cognito pool's JWKS and reads `custom:tenant_id`.
That claim — NEVER a header or request body — is the only source of tenant identity. It is what gets
pushed into Postgres `app.current_tenant`, Cube's securityContext, and the agent session metadata.

The verifier is injected so the app is testable offline; the real `CognitoJwtVerifier` checks the
signature against the pool JWKS (authored + flagged verify, never called in tests).

RBAC (the security-audit fix — "no route checks a role"):
  Roles ride the VERIFIED `cognito:groups` claim (Cognito stamps group memberships into both ID
  and access tokens; absent = no groups). `TenantClaims.groups` carries them, and
  :func:`is_tenant_admin` is the ONE place the admin policy lives — every privileged route gates
  through it (directly via :func:`require_tenant_admin`, or as the FastAPI dependency built by
  :func:`make_current_admin`). Do NOT re-derive "is this user an admin?" anywhere else.

  THE BACK-COMPAT RULE (deliberate, loud): a user with NO groups at all is treated as a tenant
  admin. Every user minted before RBAC landed has no group memberships — Lane Nick has not yet
  created the Cognito groups, and provisioning only started assigning "admin" with this change.
  Without this allowance, every existing (and every solo-tenant) user would be instantly locked
  out of their own kill switch, billing, and settings. MIGRATION STORY: once Lane Nick applies
  the Cognito group terraform and existing users are backfilled into groups, flip
  ``RBAC_STRICT=1`` on the API task — that removes the empty-groups allowance and the ONLY way
  to be admin is membership in the "admin" group. The flag is read per request, so the flip
  needs no deploy.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Protocol

from fastapi import Depends, HTTPException, Request

logger = logging.getLogger("api.auth")

# The Cognito group that grants tenant-admin rights (provisioning adds the tenant's FIRST user
# to it — signup/provisioning.py; the group itself is Lane Nick terraform).
ADMIN_GROUP = "admin"

# Strict-RBAC flag (read per request — flipping it needs no restart). Default OFF: the
# empty-groups back-compat allowance applies (see the module docstring's migration story).
# Set RBAC_STRICT=1 once the Cognito groups exist and every user has been assigned one.
ENV_RBAC_STRICT = "RBAC_STRICT"

# The honest, fixed 403 copy for a non-admin hitting an admin-gated route. Deliberately names
# the group so a locked-out user knows exactly what membership to ask their admin for.
ADMIN_REQUIRED_DETAIL = (
    "this action requires a workspace admin — ask a member of your workspace's "
    "'admin' group to perform it"
)


@dataclass(frozen=True)
class TenantClaims:
    tenant_id: str
    sub: str
    email: str | None = None
    # Verified Cognito group memberships (`cognito:groups`). Absent claim -> empty tuple.
    # Roles are derived ONLY from this — never from a header, query, or the request body
    # (the same trust posture as tenant_id).
    groups: tuple[str, ...] = ()


class JwtVerifier(Protocol):
    def verify(self, token: str) -> dict: ...
    """Return the verified claim set, or raise on invalid signature/expiry."""


class CognitoJwtVerifier:
    """Real verifier: checks the RS256 signature against the pool JWKS and the iss/aud. BETA/verify —
    construct lazily, never called in tests."""

    def __init__(self, pool_id: str, client_id: str, region: str = "us-east-1"):
        self.pool_id = pool_id
        self.client_id = client_id
        self.region = region
        self._jwks_client = None

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.pool_id}"

    def verify(self, token: str) -> dict:
        """Validate signature against the pool JWKS + iss/aud/exp. Returns the verified claims.

        Cognito ID tokens carry `aud=client_id` and the `custom:tenant_id` claim; access tokens carry
        `client_id` and `token_use=access`. We require an ID token (it has the tenant claim) — verify
        aud, and reject access tokens.
        """
        import jwt  # noqa: PLC0415 — lazy (PyJWT, runtime-only)
        from jwt import PyJWKClient  # noqa: PLC0415

        if self._jwks_client is None:
            self._jwks_client = PyJWKClient(f"{self.issuer}/.well-known/jwks.json")
        signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.client_id,
            issuer=self.issuer,
            options={"require": ["exp", "iss", "aud"]},
        )
        # Require token_use to be EXACTLY "id" (security-audit tightening): a real Cognito ID
        # token always carries token_use=id, so a token without the claim is malformed or
        # non-Cognito and must be rejected — the earlier None allowance would have admitted it.
        if claims.get("token_use") != "id":
            raise ValueError("expected a Cognito ID token (carries custom:tenant_id)")
        return claims


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return auth[7:].strip()


def _parse_groups(claims: dict) -> tuple[str, ...]:
    """The verified `cognito:groups` claim as a tuple of group names; absent/empty -> ().

    Cognito always emits a JSON list. Defensive shapes (a verifier or test fake handing back a
    single comma-joined string) are tolerated; anything unrecognizable parses to () — i.e. the
    user simply has no groups, never a 500.
    """
    raw = claims.get("cognito:groups")
    if raw is None:
        return ()
    if isinstance(raw, str):
        # Defensive: a stringified list ("a,b") from a non-Cognito serializer.
        return tuple(g.strip() for g in raw.split(",") if g.strip())
    if isinstance(raw, (list, tuple)):
        return tuple(str(g) for g in raw if g)
    return ()


def is_tenant_admin(claims: TenantClaims) -> bool:
    """THE admin policy — defined once, used by every privileged route.

    A user is a tenant admin iff:
      * "admin" is among their verified Cognito groups; OR
      * they have NO groups at all AND ``RBAC_STRICT`` is not enabled (the deliberate
        back-compat allowance for users minted before RBAC existed — see the module
        docstring's migration story; flip ``RBAC_STRICT=1`` to retire it).

    A user with groups that do NOT include "admin" (e.g. ["member"]) is never admin,
    regardless of the strict flag — assigning any group is an explicit role statement.
    """
    if ADMIN_GROUP in claims.groups:
        return True
    if claims.groups:
        return False  # explicit non-admin role(s) assigned
    # No groups at all: back-compat admin unless the operator has flipped strict mode.
    strict = os.environ.get(ENV_RBAC_STRICT, "").strip().lower() in ("1", "true", "yes", "on")
    return not strict


def require_tenant_admin(claims: TenantClaims) -> TenantClaims:
    """Raise the honest 403 unless the verified claims grant tenant-admin; else pass through.

    Usable both inside a handler body (the billing_routes plain-function style) and via the
    FastAPI dependency built by :func:`make_current_admin`.
    """
    if not is_tenant_admin(claims):
        raise HTTPException(status_code=403, detail=ADMIN_REQUIRED_DETAIL)
    return claims


def make_current_admin(current_tenant):
    """Build the admin-gated FastAPI dependency from the app's `current_tenant` dependency.

    Mirrors how mount_* functions receive `current_tenant`: each builds its own
    `current_admin = make_current_admin(current_tenant)` and puts it on WRITE routes only —
    reads stay on `current_tenant` so every tenant user can still see state (e.g. WHY their
    agents are paused). Resolution order: 401 (no/invalid token) before 403 (not admin),
    because the inner dependency runs first.
    """

    def current_admin(claims: TenantClaims = Depends(current_tenant)) -> TenantClaims:
        return require_tenant_admin(claims)

    return current_admin


def make_current_tenant(verifier: JwtVerifier, member_store=None):
    """Build the FastAPI dependency that yields TenantClaims from the verified token ONLY.

    tenant_id is read exclusively from the verified `custom:tenant_id` claim. Nothing from the request
    body, query, or any other header can set or override it.

    `member_store` (optional, the Sell roster — api.gamify_stores.PgMemberStore/InMemoryMemberStore)
    enables member-upsert-on-auth: every successful auth refreshes the caller's `members` row from
    the VERIFIED claims (sub + name/email — THE TRUST RULE, never a header/body). It is ADDITIVE and
    GUARDED — None means no-op (the default, so the unauth path and every existing test are unchanged),
    and a member-store failure is swallowed so roster bookkeeping can NEVER break authentication.
    """

    def current_tenant(request: Request) -> TenantClaims:
        token = _bearer(request)
        try:
            claims = verifier.verify(token)
        except NotImplementedError:
            raise
        except Exception:
            raise HTTPException(status_code=401, detail="invalid token")
        tenant_id = claims.get("custom:tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="no tenant in token")
        sub = claims.get("sub", "")
        email = claims.get("email")
        if member_store is not None and sub:
            # Best-effort roster refresh on the authed (verified-JWT) path only. display_name prefers
            # a friendly name claim, falling back to email; COALESCE in the store means a bare
            # presence-ping never erases a known name. Guarded: auth must succeed regardless.
            try:
                member_store.upsert(
                    tenant_id, sub,
                    display_name=claims.get("name") or claims.get("cognito:username") or email,
                )
            except Exception:  # noqa: BLE001 — roster bookkeeping must never break auth
                logger.exception("member upsert on auth failed (tenant scoped); continuing")
        return TenantClaims(
            tenant_id=tenant_id,
            sub=sub,
            email=email,
            groups=_parse_groups(claims),
        )

    return current_tenant


# Convenience re-export for typing in routes.
CurrentTenant = Depends
