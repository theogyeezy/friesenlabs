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
    DraftEmail,                                           # draft (auto)
    SendEmail, UpdateDeal, UpdateContact, CreateActivity, CreateDeal, IssueQuote,
]

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
