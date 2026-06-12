"""HubSpot full-extract client — discovers ALL properties + object types, pulls every
property + associations per record into the ``crm_records`` full-fidelity store.

Companion to :class:`ingest.connectors.hubspot.HubSpotConnector` (its OAuth auth/refresh/
vault handling is reused unchanged). stdlib ``urllib`` only; lazy imports per request so no
network (or token) is touched at import/construction time; the token is injected via
:meth:`set_token`, so the raw value never transits ``run_sync``.

MEDIA RULE (audio/photo/video/docs): file-type property VALUES (URLs/ids) are kept as text
and listed under ``properties['_media_refs']``; the bytes are NEVER fetched and the HubSpot
Files API is NEVER called. Built incrementally per HUBSPOT_FULL_EXTRACT_PLAN.md:
  item 2 — property discovery (this);  item 3 — object discovery;  item 4 — record pull.
"""
from __future__ import annotations

from dataclasses import dataclass

HUBSPOT_API_BASE = "https://api.hubapi.com"

# Property type/fieldType markers meaning "this value points at a binary file" — we keep the
# reference (URL/id) as text but NEVER download the bytes (the no-media-blobs guardrail).
_MEDIA_FIELD_TYPES = frozenset({"file"})

# Standard CRM objects + engagements, addressable directly at /crm/v3/objects/{type} and
# /crm/v3/properties/{type}. Custom objects are discovered at runtime from /crm/v3/schemas.
_STANDARD_OBJECT_TYPES = ("contacts", "companies", "deals", "tickets", "products", "line_items", "quotes")
_ENGAGEMENT_OBJECT_TYPES = ("calls", "emails", "meetings", "notes", "tasks")


@dataclass(frozen=True)
class PropertySet:
    """All property names for one object type, plus the subset that are media/file refs."""

    names: tuple[str, ...]
    media: frozenset[str]


class HubSpotFullClient:
    """Read-only HubSpot CRM v3 client for the full extract. Constructed UNAUTHENTICATED —
    the connector resolves the vaulted token and injects it via :meth:`set_token`."""

    def __init__(self, *, base_url: str = HUBSPOT_API_BASE, timeout_s: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._token: str | None = None

    def set_token(self, token: str) -> None:
        self._token = token

    # -- one GET ----------------------------------------------------------- #
    def _get(self, path: str, params: dict[str, str] | None = None) -> dict:
        import json as _json  # noqa: PLC0415 — lazy with urllib below
        import urllib.parse  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415 — lazy: no network machinery at import

        if not self._token:
            raise RuntimeError("HubSpotFullClient: no token — authenticate() must run first")
        url = f"{self._base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self._token}"}, method="GET")
        with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:  # noqa: S310 — fixed https base
            return _json.loads(resp.read().decode("utf-8"))

    # -- item 2: property discovery --------------------------------------- #
    def discover_properties(self, object_type: str) -> PropertySet:
        """``GET /crm/v3/properties/{object_type}`` → EVERY property name for the object, and
        the subset whose ``fieldType``/``type`` is a file/media ref (so the record pull keeps
        their value as a URL string but never downloads the bytes). Properties with no ``name``
        are skipped. Order is preserved (HubSpot returns a stable order)."""
        data = self._get(f"/crm/v3/properties/{object_type}")
        names: list[str] = []
        media: set[str] = set()
        for prop in data.get("results", []):
            name = prop.get("name")
            if not name:
                continue
            names.append(name)
            if prop.get("fieldType") in _MEDIA_FIELD_TYPES or prop.get("type") in _MEDIA_FIELD_TYPES:
                media.add(name)
        return PropertySet(names=tuple(names), media=frozenset(media))

    # -- item 3: object discovery ----------------------------------------- #
    def discover_object_types(self) -> tuple[str, ...]:
        """Every object type to extract: the standard objects + engagements (constants) UNION
        the tenant's custom objects from ``GET /crm/v3/schemas`` (identified by
        ``fullyQualifiedName``). Custom objects are OPTIONAL — if the schemas call fails (missing
        scope / no custom objects), the extract still runs over the standard set rather than
        dying. Order is stable (standard, then engagements, then customs); no duplicates."""
        types: list[str] = [*_STANDARD_OBJECT_TYPES, *_ENGAGEMENT_OBJECT_TYPES]
        seen = set(types)
        try:
            data = self._get("/crm/v3/schemas")
        except Exception:  # noqa: BLE001 — custom objects are optional; a missing scope must not kill the extract
            return tuple(types)
        for schema in data.get("results", []):
            name = schema.get("fullyQualifiedName") or schema.get("name") or schema.get("objectTypeId")
            if name and name not in seen:
                seen.add(name)
                types.append(name)
        return tuple(types)
