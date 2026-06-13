"""GoHighLevel (LeadConnector) full-extract client — all objects + custom objects + fields →
the source-agnostic ``crm_records`` store (``source='gohighlevel'``).

Reuses :class:`ingest.connectors.hubspot_full.Record` (the generic crm_records shape) and
``_to_millis``; lands via :class:`ingest.sinks.PgCrmRecordsSink`. stdlib ``urllib`` only; lazy
imports; token+location injected via :meth:`set_credentials`. See GOHIGHLEVEL_FULL_EXTRACT_PLAN.md
"Grounded API" for the documented shapes.

GHL v2 specifics (``services.leadconnectorhq.com``):
  - **location-scoped** — every list call carries ``locationId``;
  - **per-resource ``Version`` header** (Contacts 2021-07-28, Conversations 2021-04-15, …);
  - **pagination** via ``startAfter``/``startAfterId`` + ``limit`` (≤100); next cursors at ``meta.*``;
  - **429/Retry-After backoff** (100 req/10s, 200k/day per location).
MEDIA RULE: file/recording values are kept as URL refs (``properties['_media_refs']``); bytes are
NEVER fetched. Many exact paths/versions are ``# VERIFY`` (SPA docs) — encoded as a per-resource map.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator

from .hubspot_full import FullSyncResult, Record, _to_millis

log = logging.getLogger("ingest.connectors.gohighlevel_full")

GHL_API_BASE = "https://services.leadconnectorhq.com"
_MAX_RETRIES = 5
_DEFAULT_VERSION = "2021-07-28"

# Per-resource API Version header. contacts/conversations GROUNDED; others # VERIFY on first run.
_VERSION: dict[str, str] = {
    "contacts": "2021-07-28",
    "conversations": "2021-04-15",
    "opportunities": "2021-07-28",   # VERIFY
    "calendars": "2021-07-28",       # VERIFY
    "tasks": "2021-07-28",           # VERIFY
    "products": "2021-07-28",        # VERIFY
    "payments": "2021-07-28",        # VERIFY
    "invoices": "2021-07-28",        # VERIFY
}

# Standard objects to extract (each list endpoint is /{resource}/, location-scoped). # VERIFY paths.
_STANDARD_OBJECTS = (
    "contacts", "opportunities", "conversations", "calendars",
    "tasks", "products", "payments", "invoices",
)

# Per-object last-updated field for incremental (# VERIFY each).
_UPDATED_FIELD: dict[str, str] = {"contacts": "dateUpdated", "opportunities": "updatedAt"}

# The JSON key holding the array in a list response (the resource name; custom objects use "records").
_LIST_KEY_OVERRIDE: dict[str, str] = {"calendars": "events"}  # VERIFY

_MEDIA_EXT = (
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".mp4", ".mov", ".avi", ".webm", ".mkv",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf",
)


def _looks_media(value) -> bool:
    """A value that points at a binary file/recording (kept as a URL ref, never fetched)."""
    return isinstance(value, str) and value.lower().split("?")[0].endswith(_MEDIA_EXT)


def _updated_at(object_type: str, raw: dict):
    return raw.get(_UPDATED_FIELD.get(object_type, "updatedAt")) or raw.get("dateUpdated") or raw.get("updatedAt")


def _normalize(object_type: str, raw: dict) -> Record:
    """GHL record → source-agnostic :class:`Record`. Flattens the inline ``customFields`` array into
    ``properties`` (as ``cf_<id>``), flags media values URL-only, and pulls any ``associations``."""
    skip = {"customField", "customFields", "id", "associations"}
    props: dict = {k: v for k, v in raw.items() if k not in skip}
    media: list[str] = [k for k, v in props.items() if _looks_media(v)]
    for cf in (raw.get("customFields") or raw.get("customField") or []):
        key = cf.get("id") or cf.get("key")
        if key is None:
            continue
        val = cf.get("value") if "value" in cf else cf.get("fieldValue")
        col = f"cf_{key}"
        props[col] = val
        if _looks_media(val):
            media.append(col)
    if media:
        props["_media_refs"] = sorted(set(media))
    assoc: dict[str, list[str]] = {}
    for block in (raw.get("associations") or []):
        to_type = block.get("objectKey") or block.get("type")
        rid = block.get("recordId") or block.get("id")
        if to_type and rid:
            assoc.setdefault(str(to_type), []).append(str(rid))
    return Record(object_type, str(raw.get("id")), props, assoc, _updated_at(object_type, raw))


class GoHighLevelFullClient:
    """Read-only GHL CRM client for the full extract. Constructed UNAUTHENTICATED; the connector
    resolves the vaulted OAuth token + chosen location and injects them via :meth:`set_credentials`."""

    def __init__(self, *, base_url: str = GHL_API_BASE, timeout_s: float = 30.0, sleep=None) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._token: str | None = None
        self._location_id: str | None = None
        self._sleep = sleep  # injectable so tests never really sleep

    def set_credentials(self, token: str, location_id: str | None = None) -> None:
        self._token = token
        self._location_id = location_id

    # alias seams so the existing GoHighLevelConnector.authenticate() (reused for vault token +
    # location resolution) can inject both onto this client exactly as it does the MVP client.
    def set_token(self, token: str) -> None:
        self._token = token

    def set_location(self, location_id: str) -> None:
        self._location_id = location_id

    # -- one GET (Version header + 429/Retry-After backoff) --------------- #
    def _get(self, path: str, params: dict[str, str] | None = None, *, version: str = _DEFAULT_VERSION) -> dict:
        import json as _json  # noqa: PLC0415
        import time as _time  # noqa: PLC0415
        import urllib.error  # noqa: PLC0415
        import urllib.parse  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        if not self._token:
            raise RuntimeError("GoHighLevelFullClient: no token — authenticate() must run first")
        url = f"{self._base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = {"Authorization": f"Bearer {self._token}", "Version": version, "Accept": "application/json"}
        sleep = self._sleep or _time.sleep
        attempt = 0
        while True:
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:  # noqa: S310 — fixed https base
                    return _json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < _MAX_RETRIES:
                    retry_after = self._retry_after_seconds(exc)
                    attempt += 1
                    log.warning("gohighlevel: throttled (429) — retrying in %ss (attempt %d/%d)",
                                retry_after, attempt, _MAX_RETRIES)
                    sleep(retry_after)
                    continue
                raise

    @staticmethod
    def _retry_after_seconds(exc) -> float:
        try:
            headers = getattr(exc, "headers", None)
            value = headers.get("Retry-After") if headers else None
            return float(value) if value else 2.0
        except (TypeError, ValueError):
            return 2.0

    # -- discovery -------------------------------------------------------- #
    def discover_object_types(self) -> tuple[str, ...]:
        """Standard objects ∪ the location's CUSTOM objects (from the Custom Objects v3 Object-Schema
        API). Custom objects are OPTIONAL — a schemas-call failure falls back to the standard set."""
        types: list[str] = list(_STANDARD_OBJECTS)
        seen = set(types)
        try:
            data = self._get(f"/objects/?locationId={self._location_id}")  # VERIFY exact schema path
        except Exception:  # noqa: BLE001 — custom objects optional; a missing scope must not kill the extract
            return tuple(types)
        for schema in (data.get("objects") or data.get("schemas") or []):
            key = schema.get("key") or schema.get("objectKey") or schema.get("id")
            if key and key not in seen:
                seen.add(key)
                types.append(key)
        return tuple(types)

    def discover_fields(self, object_type: str) -> tuple[str, ...]:
        """All field names for an object (standard + custom). Best-effort from the Custom Fields V2 /
        Object-Schema API; empty tuple when unavailable (the pull still returns every property present
        on each record, so discovery is advisory, not required). # VERIFY paths."""
        try:
            data = self._get(f"/locations/{self._location_id}/customFields", version=_VERSION.get(object_type, _DEFAULT_VERSION))
        except Exception:  # noqa: BLE001 — advisory; never blocks the pull
            return ()
        names = [f.get("fieldKey") or f.get("name") or f.get("id") for f in (data.get("customFields") or [])]
        return tuple(n for n in names if n)

    # -- the full record pull --------------------------------------------- #
    def _list_path(self, object_type: str) -> str:
        return f"/{object_type}/"  # VERIFY non-contacts paths

    def _list_key(self, object_type: str) -> str:
        return _LIST_KEY_OVERRIDE.get(object_type, object_type)

    def list_records(self, object_type: str, *, location_id: str | None = None,
                     since: int | str | None = None, page_size: int = 100) -> Iterator[Record]:
        """Yield EVERY record for ``object_type`` with all properties, paginated via
        ``startAfter``/``startAfterId`` (next cursors at ``meta.*``). Incremental seeds the initial
        ``startAfter`` with ``since`` as epoch-millis (# VERIFY: startAfter sorts on dateAdded — for a
        strict modified-since a client-side guard on the updated field would be needed). Media values
        are kept as URL refs only."""
        loc = location_id or self._location_id
        path = self._list_path(object_type)
        version = _VERSION.get(object_type, _DEFAULT_VERSION)
        list_key = self._list_key(object_type)
        limit = max(1, min(int(page_size), 100))
        start_after: str | None = _to_millis(since) if since is not None else None
        start_after_id: str | None = None
        while True:
            params = {"locationId": str(loc), "limit": str(limit)}
            if start_after is not None:
                params["startAfter"] = start_after
            if start_after_id is not None:
                params["startAfterId"] = start_after_id
            page = self._get(path, params, version=version)
            rows = page.get(list_key) or page.get("records") or []
            for raw in rows:
                yield _normalize(object_type, raw)
            meta = page.get("meta") or {}
            start_after = meta.get("startAfter")
            start_after_id = meta.get("startAfterId")
            if not rows or not (start_after or start_after_id):
                return

    def search_live(self, object_type: str, *, q: str | None = None,
                    location_id: str | None = None, limit: int = 10) -> list[Record]:
        """ONE bounded page for LIVE agent queries (read-only) — never walks the whole location."""
        loc = location_id or self._location_id
        params = {"locationId": str(loc), "limit": str(max(1, min(int(limit), 100)))}
        if q:
            params["query"] = q
        page = self._get(self._list_path(object_type), params, version=_VERSION.get(object_type, _DEFAULT_VERSION))
        rows = page.get(self._list_key(object_type)) or page.get("records") or []
        return [_normalize(object_type, raw) for raw in rows]


class GoHighLevelFullConnector:
    """Drives the GHL full extract: for every object type (discovered or supplied), pull every
    record (all fields incl. flattened customFields, associations, media as refs) and UPSERT into
    ``crm_records`` via :class:`ingest.sinks.PgCrmRecordsSink` (constructed with ``source='gohighlevel'``).
    ROBUST: one object type that fails (a bad scope, a 4xx on an odd custom object) is logged by
    exception TYPE only (no token/PII) and SKIPPED so the rest of the extract still lands. The
    ``client`` must already carry the tenant's token + chosen location (``set_credentials``);
    location-scoped. Additive — lands full-fidelity ``crm_records`` alongside everything else.
    """

    def __init__(self, client: "GoHighLevelFullClient", sink) -> None:
        self._client = client
        self._sink = sink

    def sync(self, tenant_id: str, *, location_id: str | None = None,
             since: int | str | None = None,
             object_types: tuple[str, ...] | None = None) -> FullSyncResult:
        types = object_types if object_types is not None else self._client.discover_object_types()
        result = FullSyncResult()
        for object_type in types:
            try:
                records = list(self._client.list_records(
                    object_type, location_id=location_id, since=since))
            except Exception as exc:  # noqa: BLE001 — one bad object type must not kill the extract; type only (no PII)
                log.warning("gohighlevel full: object_type %s pull failed (%s) — skipped",
                            object_type, type(exc).__name__)
                result.failed_types.append(object_type)
                continue
            result.pulled += len(records)
            landed = self._sink.upsert_records(tenant_id, records)
            result.landed += landed
            result.by_type[object_type] = landed
        return result
