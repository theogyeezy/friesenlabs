"""Anthropic Admin API client (TODO INT/P0 'Implement the Anthropic Admin API client').

Implements the exact call shapes `Provisioner` (signup/provisioning.py) depends on:
  ensure_workspace(tenant_id) -> ws_id       (idempotent by name — one workspace per tenant)
  create_workspace_key(ws_id, tenant_id) -> key
  set_limits(ws_id, tenant_id)
  delete_workspace(ws_id)                    (rollback teardown)

Endpoint grounding — live docs fetched 2026-06-09
(platform.claude.com/docs/en/api/administration-api + /docs/en/manage-claude/workspaces):
  - Auth: ``x-api-key: <ADMIN key, 'sk-ant-admin...'>`` + ``anthropic-version: 2023-06-01``.
    The admin key is a DIFFERENT credential from the inference API key; only org admins can
    mint one in Console. Secrets Manager ref name: shared.config ANTHROPIC_ADMIN_KEY_SECRET.
  - CONFIRMED: POST /v1/organizations/workspaces           {"name": ...} -> {"id": "wrkspc_..."}
  - CONFIRMED: GET  /v1/organizations/workspaces?limit=&include_archived=
  - CONFIRMED: POST /v1/organizations/workspaces/{id}/archive   (there is NO DELETE — archive
    is the only teardown; it revokes the workspace's API keys and is irreversible)
  - ⚠️ NOT CONFIRMED: API-key CREATION. The docs' FAQ states "new API keys can only be created
    through the Claude Console for security reasons" — the Admin API only lists/updates keys
    (GET/POST /v1/organizations/api_keys[/{id}]). create_workspace_key() below targets an
    ASSUMED create endpoint and MUST be verified (or replaced with a Console/break-glass flow)
    before live provisioning. BLOCKED: Lane Nick.
  - ⚠️ NOT CONFIRMED: workspace spend/rate-limit WRITES. Docs show limits set in the Console
    'Limits' tab; the Rate Limits API is documented read-only. set_limits() targets an ASSUMED
    write endpoint and soft-fails (logs, returns False) so an unverified shape cannot brick
    provisioning. BLOCKED: Lane Nick.

Transport is stdlib ``urllib.request`` (no new dependency); the opener is injectable so
offline tests mock the HTTP seam completely. Unconfigured (empty admin key) raises a clean
AdminApiError before any network I/O.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"  # CONFIRMED: required anthropic-version header value

# VERIFY: assumed per-tenant default limits payload for the (unconfirmed) limits write
# endpoint. Field names are a guess; align with whatever the verified surface accepts.
DEFAULT_TENANT_LIMITS = {"monthly_spend_limit_usd": 200}

_TIMEOUT_S = 15
_PAGE_LIMIT = 100


class AdminApiError(RuntimeError):
    """Clean, typed failure for Admin API problems (unconfigured key, HTTP, bad shape)."""


def _default_opener(request: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 — https only


class AnthropicAdminClient:
    """One Anthropic workspace per tenant (the isolation boundary — CLAUDE.md tenancy model)."""

    def __init__(
        self,
        admin_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        opener=None,
        workspace_prefix: str = "uplift-tenant-",
        timeout_s: float = _TIMEOUT_S,
    ):
        self.admin_key = admin_key or ""
        self.base_url = base_url.rstrip("/")
        self.workspace_prefix = workspace_prefix
        self.timeout_s = timeout_s
        self._opener = opener or _default_opener

    # ---------------- public (Provisioner call shapes) ----------------

    def workspace_name(self, tenant_id: str) -> str:
        """Deterministic per-tenant name — the idempotency key for ensure_workspace."""
        return f"{self.workspace_prefix}{tenant_id}"

    def ensure_workspace(self, tenant_id: str) -> str:
        """Check-then-create by name: a re-delivered webhook never mints a second workspace."""
        existing = self.find_workspace(tenant_id)
        if existing is not None:
            return existing
        created = self._request(
            "POST", "/v1/organizations/workspaces",
            body={"name": self.workspace_name(tenant_id)},
        )
        ws_id = created.get("id")
        if not ws_id:
            raise AdminApiError(f"workspace create returned no id: {created!r}")
        return ws_id

    def find_workspace(self, tenant_id: str) -> str | None:
        """Return the existing workspace id for this tenant's name, or None."""
        name = self.workspace_name(tenant_id)
        after_id = None
        while True:
            query = {"limit": _PAGE_LIMIT, "include_archived": "false"}
            if after_id:
                # VERIFY: cursor param name (after_id) + page envelope (data/has_more/last_id)
                # follow the standard Anthropic list shape; confirm on the live workspaces list.
                query["after_id"] = after_id
            page = self._request("GET", "/v1/organizations/workspaces", query=query)
            for ws in page.get("data", []):
                if ws.get("name") == name:
                    return ws.get("id")
            if not page.get("has_more"):
                return None
            after_id = page.get("last_id")
            if not after_id:
                return None

    def create_workspace_key(self, workspace_id: str, tenant_id: str) -> str:
        """Mint a workspace-scoped API key; the caller stores it in Secrets Manager once.

        ⚠️ VERIFY: ASSUMED endpoint — POST /v1/organizations/api_keys
        {"name": ..., "workspace_id": ...}. Current public docs (2026-06-09) say key creation
        is Console-only and the Admin API only manages existing keys. Do not enable live
        provisioning until this is verified or replaced. BLOCKED: Lane Nick.
        """
        created = self._request(
            "POST", "/v1/organizations/api_keys",
            body={"name": f"uplift-{tenant_id}", "workspace_id": workspace_id},
        )
        # VERIFY: field carrying the one-time secret material on the (assumed) create response.
        key = created.get("key") or created.get("api_key") or created.get("raw_key")
        if not key:
            raise AdminApiError(
                "Admin API returned no key material for the new workspace key "
                f"(workspace {workspace_id}); response keys: {sorted(created)!r}"
            )
        return key

    def set_limits(self, workspace_id: str, tenant_id: str, *, limits: dict | None = None) -> bool:
        """Apply per-workspace spend/rate limits. Soft-fails (False) on HTTP failure.

        ⚠️ VERIFY: ASSUMED endpoint — POST /v1/organizations/workspaces/{id}/limits. Docs
        (2026-06-09) only show limits set via the Console 'Limits' tab and a READ-ONLY Rate
        Limits API. Until verified, an HTTP failure here logs a warning and returns False
        rather than failing (and rolling back) the whole provisioning run; the limits must
        then be set in Console. Unconfigured admin key still raises. BLOCKED: Lane Nick.
        """
        self._require_key()
        body = limits if limits is not None else dict(DEFAULT_TENANT_LIMITS)
        try:
            self._request(
                "POST", f"/v1/organizations/workspaces/{workspace_id}/limits", body=body
            )
            return True
        except AdminApiError as e:
            log.warning(
                "set_limits soft-failed for workspace %s (tenant %s) — set limits in Console "
                "until the write endpoint is verified: %s",
                workspace_id, tenant_id, e,
            )
            return False

    def delete_workspace(self, workspace_id: str) -> str:
        """Tear down a workspace (rollback path). CONFIRMED: archive is the only deletion —
        POST /v1/organizations/workspaces/{id}/archive — it revokes the workspace's API keys
        and cannot be undone."""
        archived = self._request(
            "POST", f"/v1/organizations/workspaces/{workspace_id}/archive"
        )
        return archived.get("id", workspace_id)

    # ---------------- internals ----------------

    def _require_key(self) -> None:
        if not self.admin_key:
            raise AdminApiError(
                "Anthropic Admin API key not configured — resolve the Secrets Manager ref "
                "named by ANTHROPIC_ADMIN_KEY_SECRET (shared/config.py) and inject the value; "
                "refusing to call the Admin API"
            )

    def _request(self, method: str, path: str, *, body: dict | None = None,
                 query: dict | None = None) -> dict:
        self._require_key()
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "x-api-key": self.admin_key,          # CONFIRMED: admin key header
                "anthropic-version": API_VERSION,     # CONFIRMED: required on every call
                "content-type": "application/json",
            },
        )
        try:
            response = self._opener(request, self.timeout_s)
            raw = response.read()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:500]
            except Exception:  # noqa: BLE001 — detail is best-effort
                pass
            raise AdminApiError(
                f"Admin API {method} {path} failed: HTTP {e.code}: {detail}"
            ) from e
        except AdminApiError:
            raise
        except Exception as e:  # noqa: BLE001 — network/timeout normalized
            raise AdminApiError(
                f"Admin API {method} {path} failed: {type(e).__name__}: {e}"
            ) from e
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError as e:
            raise AdminApiError(f"Admin API {method} {path} returned non-JSON body") from e
        if not isinstance(parsed, dict):
            raise AdminApiError(f"Admin API {method} {path} returned non-object JSON")
        return parsed
