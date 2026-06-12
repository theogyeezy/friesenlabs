"""Cognito multi-tenant auth — THE TRUST RULE (Build Guide Phase 9, Step 48).

The API validates the JWT signature against the Cognito pool's JWKS and reads `custom:tenant_id`.
That claim — NEVER a header or request body — is the only source of tenant identity. It is what gets
pushed into Postgres `app.current_tenant`, Cube's securityContext, and the agent session metadata.

The verifier is injected so the app is testable offline; the real `CognitoJwtVerifier` checks the
signature against the pool JWKS (authored + flagged verify, never called in tests).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from fastapi import Depends, HTTPException, Request

logger = logging.getLogger("api.auth")


@dataclass(frozen=True)
class TenantClaims:
    tenant_id: str
    sub: str
    email: str | None = None


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
        if claims.get("token_use") not in (None, "id"):
            raise ValueError("expected a Cognito ID token (carries custom:tenant_id)")
        return claims


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return auth[7:].strip()


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
        return TenantClaims(tenant_id=tenant_id, sub=sub, email=email)

    return current_tenant


# Convenience re-export for typing in routes.
CurrentTenant = Depends
