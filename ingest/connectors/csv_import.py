"""CSV import — file-based connector for contacts / companies / deals CSVs.

The api half (POST /integrations/csv/import, api/integrations_routes.py) accepts
the upload (claims-bound tenant, 5MB cap) and hands the raw bytes here. This
module:

  1. parse_csv         — decode (BOM-tolerant), sniff the delimiter (commas,
                         semicolons, tabs, pipes), DictRead the rows.
  2. detect_mapping    — map CSV headers to canonical entity fields via header
                         heuristics (synonym table), with an explicit
                         caller-supplied mapping taking precedence.
  3. rows_to_records   — validate every row (per-row error report — one bad row
                         never kills the import), derive a DETERMINISTIC ref_id
                         from the entity's natural key (contacts: email;
                         companies: domain-or-name; deals: title[+contact
                         email]) so re-imports UPSERT instead of duplicating.
  4. import_csv        — run the validated records through the EXISTING
                         tenant-scoped ingest path (`pipeline.sync_tenant`:
                         land raw + structured rows, chunk, embed, upsert into
                         `documents` — the Pg stores ride the pooled
                         SET LOCAL app.current_tenant pattern, so RLS applies).

No credential: the file IS the data and the upload is already bound to the
verified JWT tenant by the API route (THE TRUST RULE). `CsvConnector` is the
thin Connector shim that feeds pre-validated records into the pipeline;
`authenticate()` is a no-op by design (there is no vault slot for csv).

Idempotency: ref_ids are pure functions of the natural key, the documents
upsert is keyed (tenant_id, source, ref_id), and unchanged content is skipped
via the pipeline's content hash — importing the same file twice lands zero new
rows. The csv source keeps NO cursor (updated_at stays "" on every record):
each import is a full pass over the file.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable

from .base import Connector, NormalizedRecord

CSV_ENTITIES = ("contacts", "companies", "deals")

# Upload cap (enforced by the API route before bytes reach this module; the
# parser re-checks so a non-HTTP caller can't bypass it).
MAX_CSV_BYTES = 5 * 1024 * 1024


class CsvImportError(ValueError):
    """A whole-file problem (encoding, no header, unusable mapping, bad entity).

    Per-ROW problems never raise — they land in the report's `errors` list.
    """


# --------------------------------------------------------------------------- #
# Header heuristics — canonical field -> recognized header spellings
# (headers are normalized first: lowercase, _-/ collapsed to spaces, trimmed).
# --------------------------------------------------------------------------- #
_SYNONYMS: dict[str, dict[str, tuple[str, ...]]] = {
    "contacts": {
        "name": ("name", "full name", "contact name", "contact"),
        "first_name": ("first name", "firstname", "first", "given name"),
        "last_name": ("last name", "lastname", "last", "surname", "family name"),
        "email": ("email", "e mail", "email address", "work email", "primary email"),
        "phone": ("phone", "phone number", "mobile", "mobile phone", "telephone", "cell"),
        "company": ("company", "company name", "organization", "organisation",
                    "account", "employer"),
    },
    "companies": {
        "name": ("name", "company", "company name", "organization", "organisation",
                 "account name", "business name"),
        "domain": ("domain", "website", "web site", "url", "company domain", "site"),
    },
    "deals": {
        "title": ("title", "deal name", "dealname", "name", "opportunity",
                  "opportunity name", "deal"),
        "stage": ("stage", "deal stage", "dealstage", "pipeline stage", "status"),
        "amount": ("amount", "value", "deal value", "deal amount", "price", "revenue"),
        "currency": ("currency", "currency code", "deal currency"),
        "contact_email": ("contact email", "email", "primary contact email",
                          "contact e mail"),
        "company": ("company", "company name", "account"),
    },
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _norm_header(h: str) -> str:
    return re.sub(r"[\s_\-/]+", " ", (h or "").replace("\ufeff", "").strip().lower())


# --------------------------------------------------------------------------- #
# 1. parse
# --------------------------------------------------------------------------- #
def parse_csv(data: bytes) -> tuple[list[str], list[dict[str, str]], list[int]]:
    """Decode + sniff + parse. Returns (headers, rows, lines) — rows are
    header->value dicts with surrounding whitespace stripped; lines[i] is the
    1-based spreadsheet line number of rows[i] (the header is line 1; blank
    lines are skipped but still counted, so error reports point at the real
    file line). Raises CsvImportError on whole-file problems (too big,
    undecodable, empty, no usable header)."""
    if len(data) > MAX_CSV_BYTES:
        raise CsvImportError(f"file exceeds the {MAX_CSV_BYTES // (1024 * 1024)}MB cap")
    try:
        text = data.decode("utf-8-sig")  # utf-8-sig strips a BOM when present
    except UnicodeDecodeError:
        try:
            text = data.decode("latin-1")
        except UnicodeDecodeError as exc:  # pragma: no cover — latin-1 maps all bytes
            raise CsvImportError("could not decode file (use UTF-8)") from exc
    if not text.strip():
        raise CsvImportError("empty file")

    # Delimiter detection: sniff over the first lines; fall back to whichever
    # of ; or , dominates the header line (semicolon CSVs are common exports).
    sample = "\n".join(text.splitlines()[:10])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        header_line = text.splitlines()[0]
        delimiter = ";" if header_line.count(";") > header_line.count(",") else ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        raw_header = next(reader)
    except StopIteration:  # pragma: no cover — guarded by the strip() check above
        raise CsvImportError("empty file") from None
    headers = [h.replace("\ufeff", "").strip() for h in raw_header]
    if not any(headers):
        raise CsvImportError("no usable header row")

    rows: list[dict[str, str]] = []
    lines: list[int] = []
    for raw in reader:
        if not any(cell.strip() for cell in raw):
            continue  # skip blank lines (still counted by reader.line_num)
        row = {headers[i]: (raw[i].strip() if i < len(raw) else "")
               for i in range(len(headers)) if headers[i]}
        rows.append(row)
        lines.append(reader.line_num)  # 1-based; the header consumed line 1
    return headers, rows, lines


# --------------------------------------------------------------------------- #
# 2. mapping
# --------------------------------------------------------------------------- #
def detect_mapping(headers: list[str], entity: str,
                   explicit: dict[str, str] | None = None) -> dict[str, str]:
    """canonical field -> actual CSV header. Header heuristics first, then the
    caller's explicit mapping overrides field-by-field. Raises CsvImportError
    for an unknown entity, an explicit entry naming an unknown field or a
    header the file doesn't have, or a mapping missing the natural key."""
    if entity not in _SYNONYMS:
        raise CsvImportError(
            f"unknown entity {entity!r} — expected one of {', '.join(CSV_ENTITIES)}"
        )
    synonyms = _SYNONYMS[entity]
    normalized = {_norm_header(h): h for h in headers if h}

    mapping: dict[str, str] = {}
    for fld, names in synonyms.items():
        for candidate in names:
            if candidate in normalized:
                mapping[fld] = normalized[candidate]
                break

    if explicit:
        present = {h for h in headers if h}
        for fld, header in explicit.items():
            if fld not in synonyms:
                raise CsvImportError(
                    f"mapping names unknown field {fld!r} for entity {entity!r}"
                )
            if header not in present:
                raise CsvImportError(f"mapping column {header!r} not found in the CSV header")
            mapping[fld] = header

    # Natural-key requirement per entity (idempotent upserts hang off it).
    if entity == "contacts" and "email" not in mapping:
        raise CsvImportError(
            "contacts CSV needs an email column (the natural key) — none detected; "
            'pass an explicit mapping like {"email": "<your column>"}'
        )
    if entity == "companies" and "name" not in mapping and "domain" not in mapping:
        raise CsvImportError(
            "companies CSV needs a name or domain column (the natural key) — none detected"
        )
    if entity == "deals" and "title" not in mapping:
        raise CsvImportError(
            "deals CSV needs a deal-name/title column (the natural key) — none detected"
        )
    return mapping


# --------------------------------------------------------------------------- #
# 3. row validation -> NormalizedRecords (per-row error report)
# --------------------------------------------------------------------------- #
def _get(row: dict[str, str], mapping: dict[str, str], fld: str) -> str:
    header = mapping.get(fld)
    return (row.get(header, "") if header else "").strip()


def rows_to_records(
    tenant_id: str, entity: str, rows: list[dict[str, str]], mapping: dict[str, str],
    lines: list[int] | None = None,
) -> tuple[list[NormalizedRecord], list[dict]]:
    """Validate every row; return (records, errors). `errors` entries are
    {"row": <spreadsheet line number — header is line 1>, "error": <reason>}
    (`lines` carries the real file line per row from parse_csv; without it the
    rows are assumed contiguous after the header). A duplicate natural key
    within the file keeps the FIRST row and reports the later one (re-imports
    of the same file stay deterministic)."""
    records: list[NormalizedRecord] = []
    errors: list[dict] = []
    seen: dict[str, int] = {}  # ref_id -> first line number

    for idx, row in enumerate(rows):
        line = lines[idx] if lines else idx + 2  # 1-based; header is line 1
        try:
            rec = _row_record(tenant_id, entity, row, mapping)
        except _RowError as exc:
            errors.append({"row": line, "error": str(exc)})
            continue
        if rec.ref_id in seen:
            errors.append({
                "row": line,
                "error": f"duplicate of row {seen[rec.ref_id]} (natural key {rec.ref_id!r})",
            })
            continue
        seen[rec.ref_id] = line
        records.append(rec)
    return records, errors


class _RowError(ValueError):
    """One row failed validation (reported, never fatal)."""


def _row_record(tenant_id: str, entity: str, row: dict[str, str],
                mapping: dict[str, str]) -> NormalizedRecord:
    if entity == "contacts":
        return _contact_record(tenant_id, row, mapping)
    if entity == "companies":
        return _company_record(tenant_id, row, mapping)
    return _deal_record(tenant_id, row, mapping)


def _contact_record(tenant_id, row, mapping) -> NormalizedRecord:
    email = _get(row, mapping, "email").lower()
    if not email:
        raise _RowError("missing email (the contacts natural key)")
    if not _EMAIL_RE.match(email):
        raise _RowError(f"invalid email {email!r}")
    name = _get(row, mapping, "name") or " ".join(
        x for x in [_get(row, mapping, "first_name"), _get(row, mapping, "last_name")] if x
    )
    phone = _get(row, mapping, "phone")
    company = _get(row, mapping, "company")
    ref = f"csv-contact:{email}"
    crm_row = {
        "tenant_id": tenant_id,
        "company_ref_id": f"csv-company:{company.lower()}" if company else None,
        "name": name,
        "email": email,
        "phone": phone or None,
        "ref_id": ref,
        "source": "csv",
    }
    parts = [f"Contact: {name or email}", f"Email: {email}"]
    if phone:
        parts.append(f"Phone: {phone}")
    if company:
        parts.append(f"Company: {company}")
    return NormalizedRecord(
        tenant_id=tenant_id, source="csv", ref_id=ref, table="contacts",
        row=crm_row, raw=dict(row), kind="contact",
        text_blocks=[{"ref_id": ref, "kind": "contact", "text": "\n".join(parts)}],
    )


def _company_record(tenant_id, row, mapping) -> NormalizedRecord:
    name = _get(row, mapping, "name")
    domain = _get(row, mapping, "domain").lower()
    if domain:
        # tolerate URL-ish exports: strip scheme/path/www.
        domain = re.sub(r"^[a-z]+://", "", domain).split("/")[0].removeprefix("www.")
    if not name and not domain:
        raise _RowError("missing company name and domain (need at least one)")
    ref = f"csv-company:{domain or name.lower()}"
    crm_row = {
        "tenant_id": tenant_id,
        "name": name or domain,
        "domain": domain or None,
        "ref_id": ref,
        "source": "csv",
    }
    text = f"Company: {crm_row['name']}"
    if domain:
        text += f"\nDomain: {domain}"
    return NormalizedRecord(
        tenant_id=tenant_id, source="csv", ref_id=ref, table="companies",
        row=crm_row, raw=dict(row), kind="company",
        text_blocks=[{"ref_id": ref, "kind": "company", "text": text}],
    )


def _deal_record(tenant_id, row, mapping) -> NormalizedRecord:
    title = _get(row, mapping, "title")
    if not title:
        raise _RowError("missing deal title (the deals natural key)")
    raw_amount = _get(row, mapping, "amount")
    amount: float | None = None
    if raw_amount:
        try:
            # tolerate currency symbols + thousands separators ("$1,200.50")
            amount = float(re.sub(r"[^\d.\-]", "", raw_amount))
        except ValueError:
            raise _RowError(f"unparseable amount {raw_amount!r}") from None
    contact_email = _get(row, mapping, "contact_email").lower()
    if contact_email and not _EMAIL_RE.match(contact_email):
        raise _RowError(f"invalid contact email {contact_email!r}")
    stage = _get(row, mapping, "stage") or "new"
    currency = (_get(row, mapping, "currency") or "USD").upper()
    company = _get(row, mapping, "company")
    ref = f"csv-deal:{title.lower()}" + (f"|{contact_email}" if contact_email else "")
    crm_row = {
        "tenant_id": tenant_id,
        "company_ref_id": f"csv-company:{company.lower()}" if company else None,
        "contact_ref_id": f"csv-contact:{contact_email}" if contact_email else None,
        "title": title,
        "stage": stage,
        "amount": amount,
        "currency": currency,
        "ref_id": ref,
        "source": "csv",
    }
    text = f"Deal: {title}\nStage: {stage}"
    if amount is not None:
        text += f"\nAmount: {amount} {currency}"
    return NormalizedRecord(
        tenant_id=tenant_id, source="csv", ref_id=ref, table="deals",
        row=crm_row, raw=dict(row), kind="deal",
        text_blocks=[{"ref_id": ref, "kind": "deal", "text": text}],
    )


# --------------------------------------------------------------------------- #
# 4. the connector shim + the one-call import entrypoint
# --------------------------------------------------------------------------- #
class _NoSecrets:
    """csv has no vault slot — the Connector ctor still wants a provider."""

    def get_secret(self, ref: str) -> str:  # pragma: no cover — never called
        raise LookupError("csv import uses no vaulted credential")


class CsvConnector(Connector):
    """Feeds pre-validated CSV records through the standard pipeline.

    No credential by design: the upload is bound to the verified JWT tenant by
    the API route before the bytes ever reach ingest (THE TRUST RULE), so
    `authenticate()` only flips the pulled-before-auth guard."""

    source = "csv"

    def __init__(self, tenant_id, records: list[NormalizedRecord], **kwargs) -> None:
        kwargs.setdefault("secrets", _NoSecrets())
        super().__init__(tenant_id, **kwargs)
        for rec in records:
            if rec.tenant_id != tenant_id:
                raise ValueError(
                    f"cross-tenant record: connector tenant {tenant_id} "
                    f"!= record tenant {rec.tenant_id}"
                )
        self._records = records

    def authenticate(self) -> None:
        self._authed = True

    def pull(self, since_cursor: str | None) -> Iterable[NormalizedRecord]:
        self._require_auth()
        # Full pass every import (records carry updated_at="" so the csv
        # source never advances a cursor); idempotency rides the deterministic
        # ref_ids + the pipeline's content-hash skip.
        yield from self._records


@dataclass
class CsvImportReport:
    """What POST /integrations/csv/import returns (per-row error report included)."""

    entity: str
    mapping: dict[str, str]
    total_rows: int = 0
    imported: int = 0            # valid records landed through the pipeline
    rows_upserted: int = 0       # structured rows upserted by the sink
    embedded: int = 0            # chunks newly embedded into documents
    skipped_unchanged: int = 0   # chunks skipped (identical content already stored)
    errors: list[dict] = field(default_factory=list)  # [{"row": n, "error": "..."}]

    def to_dict(self) -> dict:
        return asdict(self)


def import_csv(
    tenant_id: str,
    entity: str,
    data: bytes,
    mapping: dict[str, str] | None = None,
    *,
    store,
    cursor_store,
    embedder,
    raw_sink,
    structured_sink,
) -> CsvImportReport:
    """Parse → map → validate → run through the EXISTING tenant-scoped ingest
    path (`pipeline.sync_tenant`). All stores are INJECTED — the API wiring
    passes the same Pg/S3 pieces the scheduled HubSpot sync uses (pooled
    per-op conn + SET LOCAL app.current_tenant, so RLS applies), tests pass
    the in-memory fakes. Whole-file problems raise CsvImportError; per-row
    problems come back in the report."""
    from ..pipeline import sync_tenant  # noqa: PLC0415 — avoid an import cycle

    headers, rows, lines = parse_csv(data)
    resolved = detect_mapping(headers, entity, mapping)
    records, errors = rows_to_records(tenant_id, entity, rows, resolved, lines)

    connector = CsvConnector(
        tenant_id, records, raw_sink=raw_sink, structured_sink=structured_sink,
    )
    res = sync_tenant(tenant_id, connector, embedder, store, cursor_store)

    return CsvImportReport(
        entity=entity,
        mapping=resolved,
        total_rows=len(rows),
        imported=len(records),
        rows_upserted=res.landed_rows,
        embedded=res.embedded,
        skipped_unchanged=res.skipped,
        errors=errors,
    )
