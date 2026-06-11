"""Connector registry — ONE place that knows every source the ingestion plane
speaks: hubspot | csv | gohighlevel | stripe.

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
from .hubspot import HubSpotConnector
from .stripe_data import StripeDataConnector


class _EmptyListClient:
    """Offline stub source client — every list method pulls nothing, so an
    unswitched (INGEST_REAL_STORES unset) dry run exercises the full
    auth -> pull -> land -> cursor path with zero records and zero network."""

    def __getattr__(self, name: str) -> Callable[..., list]:
        if name.startswith("list_"):
            return lambda *a, **k: []
        raise AttributeError(name)


def _real_hubspot_client():
    from .hubspot import HubSpotRestClient  # noqa: PLC0415 — lazy

    return HubSpotRestClient()


def _real_gohighlevel_client():
    from .gohighlevel import GoHighLevelRestClient  # noqa: PLC0415 — lazy

    return GoHighLevelRestClient()


def _real_stripe_client():
    from .stripe_data import StripeRestClient  # noqa: PLC0415 — lazy

    return StripeRestClient()


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
