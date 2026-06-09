"""HubSpot connector — the reference connector implementation.

DOES NOT call the real HubSpot API. The source client is INJECTED: tests pass a
fake that returns fixture contacts/companies/deals/notes. A real impl would wrap
the HubSpot REST/CRM client, but constructing it is the caller's job — importing
this module needs no network.

Normalizes HubSpot objects to the db/schema.sql shapes
(companies / contacts / deals / activities), carrying tenant_id, source='hubspot',
and ref_id (the HubSpot object id) on every row.
"""
from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from .base import Connector, NormalizedRecord


@runtime_checkable
class HubSpotClient(Protocol):
    """Minimal source-client interface the connector depends on.

    Each method returns an iterable of raw HubSpot objects (dicts). `since` is the
    high-water cursor (an ISO-8601 lastmodified timestamp); None means full pull.
    A real impl would page the HubSpot CRM Search API filtered on hs_lastmodifieddate.
    """

    def list_companies(self, since: str | None) -> Iterable[dict]: ...
    def list_contacts(self, since: str | None) -> Iterable[dict]: ...
    def list_deals(self, since: str | None) -> Iterable[dict]: ...
    def list_notes(self, since: str | None) -> Iterable[dict]: ...


# The vaulted credential reference (resolved via the injected SecretProvider).
HUBSPOT_TOKEN_SECRET_REF = "uplift/hubspot-private-app-token"


class HubSpotConnector(Connector):
    source = "hubspot"

    def __init__(self, tenant_id, *, client: HubSpotClient, **kwargs) -> None:
        super().__init__(tenant_id, **kwargs)
        self._client = client
        self._token: str | None = None

    # -- auth ------------------------------------------------------------ #
    def authenticate(self) -> None:
        # Resolve the vaulted token reference; never log/return the raw value.
        self._token = self._secrets.get_secret(HUBSPOT_TOKEN_SECRET_REF)
        if not self._token:
            raise RuntimeError("HubSpot: empty token from secret provider")
        self._authed = True

    # -- pull ------------------------------------------------------------ #
    def pull(self, since_cursor: str | None) -> Iterable[NormalizedRecord]:
        self._require_auth()
        # companies first so contacts/deals can reference company ref_ids.
        for c in self._client.list_companies(since_cursor):
            yield self._company(c)
        for c in self._client.list_contacts(since_cursor):
            yield self._contact(c)
        for d in self._client.list_deals(since_cursor):
            yield self._deal(d)
        for n in self._client.list_notes(since_cursor):
            rec = self._note(n)
            if rec is not None:
                yield rec

    # -- normalization ---------------------------------------------------- #
    @staticmethod
    def _props(obj: dict) -> dict:
        # HubSpot wraps fields under "properties"; tolerate flat fixtures too.
        return obj.get("properties", obj)

    @staticmethod
    def _updated(obj: dict, props: dict) -> str:
        return (
            obj.get("updatedAt")
            or props.get("hs_lastmodifieddate")
            or props.get("lastmodifieddate")
            or ""
        )

    def _company(self, obj: dict) -> NormalizedRecord:
        p = self._props(obj)
        ref = str(obj.get("id", p.get("id", "")))
        row = {
            "tenant_id": self.tenant_id,
            "name": p.get("name") or "",
            "domain": p.get("domain"),
            "ref_id": ref,
            "source": self.source,
        }
        text = f"Company: {row['name']}"
        if row["domain"]:
            text += f"\nDomain: {row['domain']}"
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table="companies",
            row=row,
            raw=obj,
            updated_at=self._updated(obj, p),
            kind="company",
            text_blocks=[{"ref_id": ref, "kind": "company", "text": text}],
        )

    def _contact(self, obj: dict) -> NormalizedRecord:
        p = self._props(obj)
        ref = str(obj.get("id", p.get("id", "")))
        name = " ".join(
            x for x in [p.get("firstname"), p.get("lastname")] if x
        ) or p.get("name") or ""
        row = {
            "tenant_id": self.tenant_id,
            "company_ref_id": p.get("associatedcompanyid"),
            "name": name,
            "email": p.get("email"),
            "phone": p.get("phone"),
            "ref_id": ref,
            "source": self.source,
        }
        parts = [f"Contact: {name}"]
        if row["email"]:
            parts.append(f"Email: {row['email']}")
        if row["phone"]:
            parts.append(f"Phone: {row['phone']}")
        if p.get("jobtitle"):
            parts.append(f"Title: {p['jobtitle']}")
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table="contacts",
            row=row,
            raw=obj,
            updated_at=self._updated(obj, p),
            kind="contact",
            text_blocks=[{"ref_id": ref, "kind": "contact", "text": "\n".join(parts)}],
        )

    def _deal(self, obj: dict) -> NormalizedRecord:
        p = self._props(obj)
        ref = str(obj.get("id", p.get("id", "")))
        title = p.get("dealname") or ""
        amount = p.get("amount")
        try:
            amount = float(amount) if amount not in (None, "") else None
        except (TypeError, ValueError):
            amount = None
        row = {
            "tenant_id": self.tenant_id,
            "company_ref_id": p.get("associatedcompanyid"),
            "contact_ref_id": p.get("associatedcontactid"),
            "title": title,
            "stage": p.get("dealstage") or "new",
            "amount": amount,
            "currency": p.get("deal_currency_code") or "USD",
            "ref_id": ref,
            "source": self.source,
        }
        text = f"Deal: {title}\nStage: {row['stage']}"
        if amount is not None:
            text += f"\nAmount: {amount} {row['currency']}"
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table="deals",
            row=row,
            raw=obj,
            updated_at=self._updated(obj, p),
            kind="deal",
            text_blocks=[{"ref_id": ref, "kind": "deal", "text": text}],
        )

    def _note(self, obj: dict) -> NormalizedRecord | None:
        p = self._props(obj)
        ref = str(obj.get("id", p.get("id", "")))
        body = p.get("hs_note_body") or p.get("body") or ""
        if not body:
            return None
        row = {
            "tenant_id": self.tenant_id,
            "contact_ref_id": p.get("hs_contact_id") or p.get("contact_ref_id"),
            "deal_ref_id": p.get("hs_deal_id") or p.get("deal_ref_id"),
            "kind": "note",
            "body": body,
            "ref_id": ref,
            "source": self.source,
        }
        return NormalizedRecord(
            tenant_id=self.tenant_id,
            source=self.source,
            ref_id=ref,
            table="activities",
            row=row,
            raw=obj,
            updated_at=self._updated(obj, p),
            kind="note",
            text_blocks=[{"ref_id": ref, "kind": "note", "text": body}],
        )
