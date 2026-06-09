"""Cognito multi-tenant auth — THE TRUST RULE (Build Guide Phase 9, Step 48).

The API validates the JWT signature against the Cognito pool's JWKS and reads `custom:tenant_id`.
That claim — NEVER a header or request body — is the only source of tenant identity. It is what gets
pushed into Postgres `app.current_tenant`, Cube's securityContext, and the agent session metadata.

The verifier is injected so the app is testable offline; the real `CognitoJwtVerifier` checks the
signature against the pool JWKS (authored + flagged verify, never called in tests).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fastapi import Depends, HTTPException, Request


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
        self._jwks = None

    def verify(self, token: str) -> dict:  # pragma: no cover — live JWKS, BLOCKED: needs Nick
        # VERIFY: fetch JWKS from
        #   https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/jwks.json
        # then validate signature + iss + aud + exp with PyJWT before trusting any claim.
        raise NotImplementedError("live Cognito JWKS verification — BLOCKED: needs Nick")


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return auth[7:].strip()


def make_current_tenant(verifier: JwtVerifier):
    """Build the FastAPI dependency that yields TenantClaims from the verified token ONLY.

    tenant_id is read exclusively from the verified `custom:tenant_id` claim. Nothing from the request
    body, query, or any other header can set or override it.
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
        return TenantClaims(tenant_id=tenant_id, sub=claims.get("sub", ""), email=claims.get("email"))

    return current_tenant


# Convenience re-export for typing in routes.
CurrentTenant = Depends
