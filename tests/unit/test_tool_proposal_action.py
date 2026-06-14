"""Unit: the `proposal_action` seam on the Tool base class.

A tool whose agent-facing `name` differs from the canonical gated action it stages declares
`proposal_action`; base.invoke then routes the Greenlight proposal under THAT trusted name, so the
compliance classifier and the applier dispatch agree on one discriminator. It can only point at a
more/equally restrictive registered action — it adds gating, never routes around it.
"""
from __future__ import annotations

import pytest

from agents.tools.base import InMemoryGreenlight, Policy, Tool, ToolContext


class _Aliased(Tool):
    name = "alias_tool"
    description = "stages another action"
    input_schema = {"type": "object", "properties": {}, "required": []}
    policy = Policy.ALWAYS_ASK
    proposal_action = "send_email"

    def _execute(self, ctx, **kw):
        return {"reasoning": "r", "value_at_stake": None, "body": "hi unsubscribe"}


class _Plain(Tool):
    name = "plain_tool"
    description = "stages its own name"
    input_schema = {"type": "object", "properties": {}, "required": []}
    policy = Policy.ALWAYS_ASK

    def _execute(self, ctx, **kw):
        return {"reasoning": "r", "value_at_stake": None}


@pytest.mark.unit
def test_default_proposal_action_is_none():
    assert Tool.proposal_action is None


@pytest.mark.unit
def test_proposal_action_overrides_the_proposed_action_name():
    gl = InMemoryGreenlight()
    _Aliased().invoke(ToolContext(tenant_id="t-1", agent="a", greenlight=gl))
    assert gl.queue[0]["action"] == "send_email"  # proposed under the alias, not "alias_tool"


@pytest.mark.unit
def test_no_proposal_action_proposes_under_the_tools_own_name():
    gl = InMemoryGreenlight()
    _Plain().invoke(ToolContext(tenant_id="t-1", agent="a", greenlight=gl))
    assert gl.queue[0]["action"] == "plain_tool"
