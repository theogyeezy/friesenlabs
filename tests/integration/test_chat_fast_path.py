"""Integration: the Tier-0 knowledge fast path (2026-06-12).

With a router injected, a knowledge-shaped ask is answered DIRECTLY by the grounded RAG path —
the Managed-Agents session is never touched (no coordinator inference, no delegation, no worker
round-trips: seconds instead of minutes). Crew-shaped asks route to the coordinator exactly as
before, and a Conversation WITHOUT a router keeps the status quo.
"""
from datetime import date

import pytest

from agents.runtime import Session
from conv.router import HeuristicRouter
from conv.session import Conversation

TODAY = date(2026, 6, 12)


class StubManagedRuntime:
    def __init__(self, response=None):
        self.response = response or {"answer": "coordinator prose", "delegations": [],
                                      "pending_approvals": []}
        self.sent = []

    def create_session(self, coordinator_id, tenant_id, vault_id=None, environment_id=None):
        return Session(id="sess-1", tenant_id=tenant_id, coordinator_id=coordinator_id,
                       metadata={"tenant_id": tenant_id, "environment_id": environment_id})

    def send_message(self, session, message):
        self.sent.append(message)
        return {"session_id": session.id, "tenant_id": session.tenant_id, **self.response}


class FakeRag:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search(self, *, tenant_id, query, limit=8):
        self.calls.append((tenant_id, query))
        return self.hits


def _convo(rt, **kw):
    return Conversation(tenant_id="tenant-A", today=TODAY, runtime=rt,
                        coordinator_id="coord-A", environment_id="env-A", **kw)


_HITS = [{"ref_id": "demo:kb:pricing#0", "source": "upload",
          "content": "Discounts cap at 15% without VP approval.", "score": 0.9}]


@pytest.mark.integration
def test_knowledge_ask_answers_fast_without_touching_the_runtime():
    rt = StubManagedRuntime()
    rag = FakeRag(_HITS)
    turn = _convo(rt, rag=rag, router=HeuristicRouter()).send("What is our discount policy?")
    assert rt.sent == []  # THE point: no MA session round-trip on the fast lane
    assert "Discounts cap at 15%" in turn.answer
    assert [c["source_ref"] for c in turn.citations] == ["demo:kb:pricing#0"]
    assert turn.grounding_status == "grounded"
    assert turn.settled is True


@pytest.mark.integration
def test_crew_ask_still_routes_to_the_coordinator():
    rt = StubManagedRuntime()
    turn = _convo(rt, rag=FakeRag(_HITS), router=HeuristicRouter()).send(
        "Send a follow-up email to the Acme lead")
    assert rt.sent == ["Send a follow-up email to the Acme lead"]
    assert turn.answer == "coordinator prose"


@pytest.mark.integration
def test_no_router_keeps_the_status_quo():
    rt = StubManagedRuntime()
    turn = _convo(rt, rag=FakeRag(_HITS)).send("What is our discount policy?")
    assert rt.sent == ["What is our discount policy?"]  # coordinator path, as today


@pytest.mark.integration
def test_fast_lane_without_rag_falls_through_to_the_crew():
    rt = StubManagedRuntime()
    turn = _convo(rt, router=HeuristicRouter()).send("What is our discount policy?")
    assert rt.sent == ["What is our discount policy?"]  # no corpus client -> crew handles it
