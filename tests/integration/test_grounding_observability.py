"""Integration: grounding observability on /chat turns (knowledge audit P0, 2026-06-11).

A customer must be able to tell the difference between "RAG ran and your corpus is empty",
"RAG ran and grounded the answer", "nothing survived grounding", and "grounding isn't wired" —
previously an empty-corpus answer was indistinguishable from a generic refusal. Every Turn now
carries `grounding_status` + `retrieved_count` (None on turns that deliberately skip retrieval).
"""
from datetime import date

import pytest

from agents.runtime import Session, get_runtime
from conv.session import Conversation

TODAY = date(2026, 6, 11)


class StubManagedRuntime:
    """Replays a canned digest — NOT a FakeRuntime, so the live coordinator path runs."""

    def __init__(self, response: dict):
        self.response = dict(response)

    def create_session(self, coordinator_id, tenant_id, vault_id=None, environment_id=None):
        return Session(
            id="sess-1", tenant_id=tenant_id, coordinator_id=coordinator_id,
            metadata={"tenant_id": tenant_id, "environment_id": environment_id},
        )

    def send_message(self, session, message):
        return {"session_id": session.id, "tenant_id": session.tenant_id, **self.response}


class FakeRag:
    def __init__(self, hits_by_tenant):
        self.hits_by_tenant = hits_by_tenant

    def search(self, *, tenant_id, query, limit=8):
        return self.hits_by_tenant.get(tenant_id, [])


def _convo(runtime, **kw):
    return Conversation(
        tenant_id="tenant-A", today=TODAY, runtime=runtime,
        coordinator_id="coord-A", environment_id="env-A", **kw,
    )


def _rag_a():
    return FakeRag({"tenant-A": [{"ref": "doc:1", "snippet": "Acme renewed for $50k in Q1."}]})


def _live_rt(answer="Acme looks healthy.", pending=()):
    return StubManagedRuntime(
        {"answer": answer, "delegations": [], "pending_approvals": list(pending)})


# --------------------------------------------------------------------------- live path
@pytest.mark.integration
def test_live_grounded_turn_reports_status_and_count():
    turn = _convo(_live_rt(), rag=_rag_a()).send("How is Acme doing?")
    assert turn.grounding_status == "grounded"
    assert turn.retrieved_count == 1
    d = turn.as_dict()
    assert d["grounding_status"] == "grounded"
    assert d["retrieved_count"] == 1


@pytest.mark.integration
def test_live_empty_corpus_reports_no_sources_found():
    turn = _convo(_live_rt(), rag=FakeRag({})).send("How is Acme doing?")
    assert turn.grounding_status == "no_sources_found"
    assert turn.retrieved_count == 0
    assert turn.citations == []


@pytest.mark.integration
def test_live_without_rag_client_reports_unavailable():
    turn = _convo(_live_rt()).send("How is Acme doing?")  # no rag wired
    assert turn.grounding_status == "unavailable"
    assert turn.retrieved_count is None


@pytest.mark.integration
def test_live_action_turn_skips_grounding_status():
    pending = [{"status": "pending", "tool": "send_email",
                "input": {"to": "a@b.c"}, "custom_tool_use_id": "ctu_1"}]
    turn = _convo(_live_rt(answer="", pending=pending), rag=_rag_a()).send("email the Acme lead")
    assert turn.grounding_status is None  # retrieval deliberately skipped — not a corpus signal
    assert turn.retrieved_count is None


# --------------------------------------------------------------------------- facade path
@pytest.mark.integration
def test_facade_knowledge_turn_reports_grounded():
    convo = Conversation(tenant_id="tenant-A", today=TODAY,
                         runtime=get_runtime({"runtime": "fake"}), rag=_rag_a())
    turn = convo.send("How is the Acme account doing?")
    assert turn.grounding_status == "grounded"
    assert turn.retrieved_count == 1


@pytest.mark.integration
def test_facade_knowledge_turn_without_rag_reports_unavailable():
    convo = Conversation(tenant_id="tenant-A", today=TODAY,
                         runtime=get_runtime({"runtime": "fake"}))
    turn = convo.send("How is the Acme account doing?")
    assert turn.grounding_status == "unavailable"
    assert turn.retrieved_count is None
