"""Connector registry — ONE place that knows every source the ingestion plane
speaks: hubspot | csv | gohighlevel | stripe | salesforce | microsoft | google |
pipedrive.

Two consumers:
  * ingest/run_sync.py builds sync connectors by name (`build_connector(name,
    tenant_id, ...)`) — for both the scheduled path and API-kicked syncs.
  * api/integrations_routes.py lists connectors + per-tenant status. The API
    module keeps its own SELF-CONTAINED metadata mirror (KNOWN_INTEGRATIONS)
    because the production API image must boot without ingest/ present (see
    the HOTFIX note there + tests/unit/test_integrations_image_fileset.py);
    tests/unit/test_connector_registry.py asserts the two stay in sync.

`kind` semantics:
  "sync" — credentialed pull connectors (vault slot uplift/{tenant}/{name},
           incremental cursor, runnable by run_sync / the sync route).
  "file" — push-style imports (csv): no vault slot, no cursor; data arrives
           via POST /integrations/csv/import. NOT buildable here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .base import Connector
from .gohighlevel import GoHighLevelConnector
from .google import GoogleConnector
from .hubspot import HubSpotConnector
from .microsoft import MicrosoftConnector
from .pipedrive import PipedriveConnector
from .salesforce import SalesforceConnector
from .stripe_data import StripeDataConnector


class _EmptyListClient:
    """Offline stub source client — every list method pulls nothing, so an
    unswitched (INGEST_REAL_STORES unset) dry run exercises the full
    auth -> pull -> land -> cursor path with zero records and zero network."""

    def __getattr__(self, name: str) -> Callable[..., list]:
        if name.startswith("list_"):
            return lambda *a, **k: []
        raise AttributeError(name)


class _EmptyDeltaClient:
    """Offline stub for delta-query connectors (microsoft) — every delta returns
    no items and an empty deltaLink, so a dry run exercises the full sync path
    with zero records and zero network."""

    def set_token(self, token: str) -> None:  # connector calls this in authenticate()
        pass

    def delta(self, resource: str, delta_link):
        return [], ""


class _EmptySyncClient:
    """Offline stub for sync-token connectors (google) — every sync returns no
    items and an empty syncToken, so a dry run exercises the full sync path with
    zero records and zero network."""

    def set_token(self, token: str) -> None:  # connector calls this in authenticate()
        pass

    def sync(self, resource: str, sync_token):
        return [], ""


def _real_hubspot_client():
    from .hubspot import HubSpotRestClient  # noqa: PLC0415 — lazy

    return HubSpotRestClient()


def _real_gohighlevel_client():
    from .gohighlevel import GoHighLevelRestClient  # noqa: PLC0415 — lazy

    return GoHighLevelRestClient()


def _real_salesforce_client():
    from .salesforce import SalesforceRestClient  # noqa: PLC0415 — lazy

    return SalesforceRestClient()


def _real_stripe_client():
    from .stripe_data import StripeRestClient  # noqa: PLC0415 — lazy

    return StripeRestClient()


def _real_microsoft_client():
    from .microsoft import MicrosoftGraphRestClient  # noqa: PLC0415 — lazy

    return MicrosoftGraphRestClient()


def _real_google_client():
    from .google import GoogleRestClient  # noqa: PLC0415 — lazy

    return GoogleRestClient()


def _real_pipedrive_client():
    from .pipedrive import PipedriveRestClient  # noqa: PLC0415 — lazy

    return PipedriveRestClient()


@dataclass(frozen=True)
class ConnectorSpec:
    name: str
    label: str
    category: str
    description: str
    kind: str  # "sync" (credentialed pull) | "file" (push import — csv)
    experimental: bool = False
    connector_cls: type[Connector] | None = None
    client_arg: str = "client"
    real_client_factory: Callable[[], Any] | None = None
    stub_client_factory: Callable[[], Any] = field(default=_EmptyListClient)


REGISTRY: dict[str, ConnectorSpec] = {
    "hubspot": ConnectorSpec(
        name="hubspot",
        label="HubSpot",
        category="CRM & Marketing",
        description=(
            "Sync companies, contacts, deals and notes from HubSpot CRM into "
            "your Uplift data plane (read-only — Uplift never writes back)."
        ),
        kind="sync",
        connector_cls=HubSpotConnector,
        real_client_factory=_real_hubspot_client,
    ),
    "csv": ConnectorSpec(
        name="csv",
        label="CSV Import",
        category="Files & Imports",
        description=(
            "Import contacts, companies or deals from a CSV export (up to 5MB). "
            "Column mapping is auto-detected and can be overridden per upload."
        ),
        kind="file",
    ),
    "gohighlevel": ConnectorSpec(
        name="gohighlevel",
        label="GoHighLevel",
        category="CRM & Marketing",
        description=(
            "EXPERIMENTAL: sync contacts and opportunities from a GoHighLevel "
            "location (read-only — Uplift never writes back)."
        ),
        kind="sync",
        experimental=True,
        connector_cls=GoHighLevelConnector,
        real_client_factory=_real_gohighlevel_client,
    ),
    "salesforce": ConnectorSpec(
        name="salesforce",
        label="Salesforce",
        category="CRM & Marketing",
        description=(
            "EXPERIMENTAL: sync accounts, contacts, leads, opportunities and "
            "activities from Salesforce via OAuth + SOQL (read-only — Uplift "
            "never writes back)."
        ),
        kind="sync",
        experimental=True,
        connector_cls=SalesforceConnector,
        real_client_factory=_real_salesforce_client,
    ),
    "stripe": ConnectorSpec(
        name="stripe",
        label="Stripe (revenue data)",
        category="Payments & Revenue",
        description=(
            "Pull customers, subscriptions and invoices from YOUR Stripe account "
            "for revenue views (read-only; connect your own restricted key)."
        ),
        kind="sync",
        connector_cls=StripeDataConnector,
        real_client_factory=_real_stripe_client,
    ),
    "microsoft": ConnectorSpec(
        name="microsoft",
        label="Microsoft 365",
        category="CRM & Marketing",
        description=(
            "EXPERIMENTAL: sync mail, calendar and contacts from Microsoft 365 "
            "(Outlook/Exchange) via Microsoft Graph delta queries "
            "(read-only — Uplift never writes back)."
        ),
        kind="sync",
        experimental=True,
        connector_cls=MicrosoftConnector,
        real_client_factory=_real_microsoft_client,
        stub_client_factory=_EmptyDeltaClient,
    ),
    "google": ConnectorSpec(
        name="google",
        label="Google (Calendar + Contacts)",
        category="CRM & Marketing",
        description=(
            "EXPERIMENTAL: sync calendar events and contacts from Google "
            "(Calendar + People APIs) via incremental sync tokens "
            "(read-only — Uplift never writes back). Gmail is not included."
        ),
        kind="sync",
        experimental=True,
        connector_cls=GoogleConnector,
        real_client_factory=_real_google_client,
        stub_client_factory=_EmptySyncClient,
    ),
    "pipedrive": ConnectorSpec(
        name="pipedrive",
        label="Pipedrive",
        category="CRM & Marketing",
        description=(
            "EXPERIMENTAL: sync persons, organizations, deals and activities from "
            "Pipedrive via OAuth + the API v2 incremental endpoints "
            "(read-only — Uplift never writes back)."
        ),
        kind="sync",
        experimental=True,
        connector_cls=PipedriveConnector,
        real_client_factory=_real_pipedrive_client,
    ),
}

#: names runnable as a pull sync (run_sync / POST /integrations/{name}/sync).
SYNC_SOURCES: tuple[str, ...] = tuple(
    n for n, s in REGISTRY.items() if s.kind == "sync"
)


def get_spec(name: str) -> ConnectorSpec:
    spec = REGISTRY.get(name)
    if spec is None:
        raise KeyError(
            f"unknown connector {name!r} — known: {', '.join(sorted(REGISTRY))}"
        )
    return spec


def build_sync_connector(
    name: str,
    tenant_id: str,
    *,
    secrets,
    raw_sink,
    structured_sink,
    client: Any = None,
    real_client: bool = False,
) -> Connector:
    """Construct a sync connector by registry name.

    `client` (tests) wins; otherwise the real REST client when `real_client`
    (run_sync real mode) else the empty offline stub. "file" connectors (csv)
    are NOT buildable here — csv data arrives via the import endpoint.
    """
    spec = get_spec(name)
    if spec.kind != "sync" or spec.connector_cls is None:
        raise ValueError(
            f"connector {name!r} is not a pull-sync source "
            "(csv imports ride POST /integrations/csv/import)"
        )
    if client is None:
        client = (
            spec.real_client_factory() if (real_client and spec.real_client_factory)
            else spec.stub_client_factory()
        )
    kwargs = {
        "secrets": secrets,
        "raw_sink": raw_sink,
        "structured_sink": structured_sink,
        spec.client_arg: client,
    }
    return spec.connector_cls(tenant_id, **kwargs)
