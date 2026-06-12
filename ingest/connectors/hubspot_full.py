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

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

log = logging.getLogger("ingest.connectors.hubspot_full")

HUBSPOT_API_BASE = "https://api.hubapi.com"

# Which associations to request inline per object type (List API). Conservative core links;
# unsupported targets would 400 the call, so a type's pull is wrapped to fall back gracefully.
# VERIFY against live association labels on first run.
_ASSOCIATIONS: dict[str, tuple[str, ...]] = {
    "contacts": ("companies", "deals"),
    "companies": ("contacts", "deals"),
    "deals": ("contacts", "companies", "line_items"),
    "tickets": ("contacts", "companies"),
    "calls": ("contacts", "companies", "deals"),
    "emails": ("contacts", "companies", "deals"),
    "meetings": ("contacts", "companies", "deals"),
    "notes": ("contacts", "companies", "deals"),
    "tasks": ("contacts", "companies", "deals"),
}


def _assoc_for(object_type: str) -> tuple[str, ...]:
    return _ASSOCIATIONS.get(object_type, ())

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


@dataclass(frozen=True)
class Record:
    """One normalized CRM record headed for crm_records: the FULL property bag (media kept as
    URL/id refs only, flagged under properties['_media_refs']), the flattened association graph,
    the provider id, and the provider last-modified timestamp."""

    object_type: str
    source_ref_id: str
    properties: dict
    associations: dict
    updated_at: str | None


def _lastmod_prop(object_type: str) -> str:
    """HubSpot's last-modified property — contacts are the odd one out (CRM v3 docs)."""
    return "lastmodifieddate" if object_type == "contacts" else "hs_lastmodifieddate"


def _to_millis(since: int | str) -> str:
    """Cursor → epoch-millis STRING for the Search date filter. HubSpot's Search filter on a
    datetime property wants epoch millis, NOT an ISO-8601 string — feeding ISO is the bug that
    broke every incremental sync. Accepts an int/numeric-str (already millis) or an ISO-8601
    string (converted)."""
    if isinstance(since, (int, float)):
        return str(int(since))
    s = str(since)
    if s.isdigit():
        return s
    from datetime import datetime, timezone  # noqa: PLC0415 — lazy

    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return str(int(dt.timestamp() * 1000))


def _normalize(object_type: str, raw: dict, media_props: frozenset[str]) -> Record:
    """Provider record → :class:`Record`. Media properties keep their value (URL/id) but are
    listed under ``properties['_media_refs']`` so downstream NEVER treats them as fetchable text;
    the bytes are never pulled. Associations are flattened to ``{toType: [ids]}``."""
    props = dict(raw.get("properties") or {})
    media_present = sorted(n for n in media_props if props.get(n) not in (None, ""))
    if media_present:
        props["_media_refs"] = media_present
    assoc: dict[str, list[str]] = {}
    for to_type, block in (raw.get("associations") or {}).items():
        ids = [r.get("id") or r.get("toObjectId") for r in (block.get("results") or [])]
        flat = [str(i) for i in ids if i]
        if flat:
            assoc[to_type] = flat
    return Record(
        object_type=object_type,
        source_ref_id=str(raw.get("id")),
        properties=props,
        associations=assoc,
        updated_at=raw.get("updatedAt") or props.get(_lastmod_prop(object_type)),
    )


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

    # -- one POST (Search API) -------------------------------------------- #
    def _post(self, path: str, payload: dict) -> dict:
        import json as _json  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        if not self._token:
            raise RuntimeError("HubSpotFullClient: no token — authenticate() must run first")
        req = urllib.request.Request(
            f"{self._base_url}{path}",
            data=_json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            method="POST",
        )
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

    # -- item 4: full record pull ----------------------------------------- #
    def list_records(
        self,
        object_type: str,
        prop_set: PropertySet,
        *,
        since: int | str | None = None,
        associated_types: tuple[str, ...] = (),
        page_size: int = 100,
    ) -> Iterator[Record]:
        """Yield EVERY record for ``object_type`` with ALL properties, paginated.

        - **Full pull** (``since is None``): List API ``GET /crm/v3/objects/{type}`` with the
          association graph requested inline.
        - **Incremental** (``since`` given): Search API filtered on the last-modified property
          ``>= epoch-millis(since)`` — the epoch-millis filter is the sync-bug fix (ISO 400'd).

        Paginates via ``paging.next.after``. Media properties keep their URL/id value but are
        flagged under ``properties['_media_refs']`` — the bytes are never fetched and the HubSpot
        Files API is never touched."""
        lastmod = _lastmod_prop(object_type)
        props = list(prop_set.names)
        limit = min(int(page_size), 200)

        if since is None:
            def fetch(after: str | None) -> dict:
                params = {"properties": ",".join(props), "limit": str(limit), "archived": "false"}
                if associated_types:
                    params["associations"] = ",".join(associated_types)
                if after:
                    params["after"] = after
                return self._get(f"/crm/v3/objects/{object_type}", params)
        else:
            since_ms = _to_millis(since)

            def fetch(after: str | None) -> dict:
                body: dict = {
                    "properties": props,
                    "sorts": [{"propertyName": lastmod, "direction": "ASCENDING"}],
                    "limit": limit,
                    "filterGroups": [
                        {"filters": [{"propertyName": lastmod, "operator": "GTE", "value": since_ms}]}
                    ],
                }
                if after:
                    body["after"] = after
                return self._post(f"/crm/v3/objects/{object_type}/search", body)

        after: str | None = None
        while True:
            page = fetch(after)
            for raw in page.get("results", []):
                yield _normalize(object_type, raw, prop_set.media)
            after = (page.get("paging", {}).get("next") or {}).get("after")
            if not after:
                return

    # -- live (bounded) search for the agent MCP tools (item 10) ---------- #
    def search_live(self, object_type: str, *, q: str | None = None,
                    properties: tuple[str, ...] | None = None, limit: int = 10) -> list[Record]:
        """ONE bounded Search page for LIVE agent queries (read-only) — never paginates the whole
        CRM. `q` is HubSpot's full-text `query`. Returns normalized :class:`Record`s; values come
        back as text (media URLs are NOT fetched — the no-blobs guardrail holds on this path too)."""
        body: dict = {"limit": max(1, min(int(limit), 100))}
        if properties:
            body["properties"] = list(properties)
        if q:
            body["query"] = q
        page = self._post(f"/crm/v3/objects/{object_type}/search", body)
        return [_normalize(object_type, raw, frozenset()) for raw in page.get("results", [])]


@dataclass
class FullSyncResult:
    """Outcome of one full extract: total records pulled + landed, the per-object-type landed
    counts, and how many object types failed (skipped, not fatal)."""

    pulled: int = 0
    landed: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    failed_types: list[str] = field(default_factory=list)


class HubSpotFullConnector:
    """Drives the full extract: for every object type (discovered or supplied), discover ALL its
    properties, pull every record (all properties + associations, media as refs only), and UPSERT
    into ``crm_records`` via :class:`ingest.sinks.PgCrmRecordsSink`. ROBUST: one object type that
    fails (bad scope, a 400 on an odd custom object) is logged by TYPE only — never the message,
    so no token/PII leaks — and SKIPPED so the rest of the extract still lands.

    The ``client`` must already carry the tenant's token (``set_token``); the ``sink`` is bound to
    the same tenant via its own ``SET LOCAL``. Additive: the existing typed contacts/companies/deals
    + vector-embedding path is untouched — this lands the full-fidelity ``crm_records`` alongside it.
    """

    def __init__(self, client: HubSpotFullClient, sink) -> None:
        self._client = client
        self._sink = sink

    def sync(self, tenant_id: str, *, since: int | str | None = None,
             object_types: tuple[str, ...] | None = None) -> FullSyncResult:
        types = object_types if object_types is not None else self._client.discover_object_types()
        result = FullSyncResult()
        for object_type in types:
            try:
                prop_set = self._client.discover_properties(object_type)
                records = list(self._client.list_records(
                    object_type, prop_set, since=since, associated_types=_assoc_for(object_type)))
            except Exception as exc:  # noqa: BLE001 — one bad object type must not kill the extract; type only (no PII)
                log.warning("hubspot full: object_type %s pull failed (%s) — skipped",
                            object_type, type(exc).__name__)
                result.failed_types.append(object_type)
                continue
            result.pulled += len(records)
            landed = self._sink.upsert_records(tenant_id, records)
            result.landed += landed
            result.by_type[object_type] = landed
        return result
