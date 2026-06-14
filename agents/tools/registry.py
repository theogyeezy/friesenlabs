"""The trusted server-side tool registry.

THE SECURITY CONTRACT: the action gate must derive whether an action is side-effecting (and its comms
channel) from the TOOL'S OWN DEFINITION — never from a client-supplied flag. A request that names
`send_email` is side-effecting because `SendEmail.policy is ALWAYS_ASK`, full stop; a forged
`side_effecting: false` in the body cannot change that. This registry is also how `AgentSpec.tools`
name-strings resolve to executable `Tool` classes.
"""
from __future__ import annotations

from .base import Policy, Tool
from .build_view import BuildView
from .ghl_live import GhlFields, GhlObjectTypes, GhlSearch
from .hubspot_live import HubSpotObjectTypes, HubSpotProperties, HubSpotSearch
from .readonly import QueryCube, ReadCrm, SearchRag
from .run_model import RunModel
from .sideeffecting import (
    CreateActivity,
    CreateDeal,
    DraftEmail,
    IssueQuote,
    SendEmail,
    UpdateContact,
    UpdateDeal,
)

_TOOL_CLASSES: list[type[Tool]] = [
    SearchRag, QueryCube, ReadCrm, RunModel, BuildView,  # read-only (auto)
    HubSpotObjectTypes, HubSpotProperties, HubSpotSearch,  # live HubSpot (read-only, auto)
    GhlObjectTypes, GhlFields, GhlSearch,                 # live GoHighLevel (read-only, auto)
    # ALWAYS_ASK (Greenlight-gated). draft_email is the drafting specialists' affordance and stages
    # the canonical send_email approval (proposal_action) — that is why send_email itself stays
    # registry-only/unserved (no agent grants it; the real send is the post-approval api/control path).
    DraftEmail, SendEmail, UpdateDeal, UpdateContact, CreateActivity, CreateDeal, IssueQuote,
]


def tenant_hubspot_client(tenant_id: str, secrets):
    """Resolve a tenant's LIVE HubSpotFullClient (token from the vault, REUSING the connector auth)
    for the live HubSpot tools — or None if the tenant has no vaulted HubSpot credential (not
    connected) or the creds can't be read. Honest degradation: the tools then report not_connected.
    Errors are swallowed by TYPE (no token/PII) so a missing credential never throws into the loop."""
    import logging  # noqa: PLC0415

    from ingest.connectors.hubspot import HubSpotConnector  # noqa: PLC0415 — lazy, avoid import cost
    from ingest.connectors.hubspot_full import HubSpotFullClient  # noqa: PLC0415

    client = HubSpotFullClient()
    try:
        HubSpotConnector(
            tenant_id, client=client, secrets=secrets, raw_sink=None, structured_sink=None,
        ).authenticate()
    except Exception as exc:  # noqa: BLE001 — not connected / unreadable creds → no live client
        logging.getLogger("agents.tools").info(
            "hubspot live tool: no client for tenant (%s) — not connected", type(exc).__name__)
        return None
    return client


def tenant_ghl_client(tenant_id: str, secrets):
    """Resolve a tenant's LIVE GoHighLevelFullClient (token + location_id from the vault, REUSING the
    connector auth) for the live GHL tools — or None if the tenant has no vaulted GoHighLevel
    credential (not connected) or the creds can't be read. Honest degradation: the tools then report
    not_connected. Errors are swallowed by TYPE (no token/PII) so a missing credential never throws
    into the loop."""
    import logging  # noqa: PLC0415

    from ingest.connectors.gohighlevel import GoHighLevelConnector  # noqa: PLC0415 — lazy
    from ingest.connectors.gohighlevel_full import GoHighLevelFullClient  # noqa: PLC0415

    client = GoHighLevelFullClient()
    try:
        GoHighLevelConnector(
            tenant_id, client=client, secrets=secrets, raw_sink=None, structured_sink=None,
        ).authenticate()
    except Exception as exc:  # noqa: BLE001 — not connected / unreadable creds → no live client
        logging.getLogger("agents.tools").info(
            "ghl live tool: no client for tenant (%s) — not connected", type(exc).__name__)
        return None
    return client

# name -> Tool class
TOOL_REGISTRY: dict[str, type[Tool]] = {cls.name: cls for cls in _TOOL_CLASSES}


def get_tool(name: str) -> type[Tool] | None:
    return TOOL_REGISTRY.get(name)


def resolve(name: str) -> Tool:
    """Instantiate the tool named `name` (for AgentSpec.tools resolution). Raises on unknown name."""
    cls = TOOL_REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"unknown tool: {name!r}")
    return cls()


def tool_meta(name: str) -> dict:
    """The server-side truth about a named action: is it side-effecting, and on what channel.

    Raises KeyError for an unknown tool (callers MUST reject unknown tools, never default-allow).
    """
    cls = TOOL_REGISTRY[name]
    return {
        "side_effecting": cls.policy is Policy.ALWAYS_ASK,
        "channel": getattr(cls, "channel", None),
    }
