"""Integration: grounded citations on the REAL (non-fake) runtime path.

`_handle_knowledge` (the citation-invariant RAG path) used to run ONLY on FakeRuntime — live
Managed-Agents chat answers carried zero citations. Now `_handle_coordinator` runs the SAME
`_grounded_answer` path (conv.rag.answer over the tenant-scoped rag client) for knowledge-shaped
turns (nothing queued for approval), so:

  - live answers carry citations whose source_ref EXISTS in the retrieved set (THE invariant);
  - a hallucinated/uncited claim is dropped — never surfaced as grounded — on BOTH runtimes;
  - action turns (pending approvals) skip retrieval entirely;
  - with no rag client wired, behavior is byte-identical to before (no citations).

The stub replays a canned ManagedAgentsRuntime.send_message digest, exactly like
test_conversation_coordinator_routing.py.
"""
from datetime import date

import pytest

from agents.runtime import Session, get_runtime
from conv.session import Conversation

TODAY = date(2026, 6, 10)


class StubManagedRuntime:
    """Replays a canned ManagedAgentsRuntime.send_message digest — NOT a FakeRuntime, so the
    conversation takes the coordinator-driven (live) path."""

    def __init__(self, response: dict):
        self.response = dict(response)
        self.sent: list[tuple[str, str]] = []

    def create_session(self, coordinator_id, tenant_id, vault_id=None, environment_id=None):
        return Session(
            id="sess-1", tenant_id=tenant_id, coordinator_id=coordinator_id,
            metadata={"tenant_id": tenant_id, "environment_id": environment_id},
        )

    def send_message(self, session, message):
        self.sent.append((session.id, message))
        return {"session_id": session.id, "tenant_id": session.tenant_id, **self.response}


class FakeRag:
    def __init__(self, hits_by_tenant):
        self.hits_by_tenant = hits_by_tenant
        self.calls: list[tuple[str, str]] = []

    def search(self, *, tenant_id, query, limit=8):
        self.calls.append((tenant_id, query))
        return self.hits_by_tenant.get(tenant_id, [])


class FakeSynth:
    """Cites the retrieved chunks (grounded) plus one hallucination to prove it is dropped."""

    def synthesize(self, *, question, chunks):
        claims = [{"text": c["snippet"], "source_refs": [c["ref"]]} for c in chunks]
        claims.append({"text": "Acme is secretly about to churn.",
                       "source_refs": ["doc:hallucinated"]})
        return {"summary": None, "claims": claims}


def _convo(runtime, **kw):
    return Conversation(
        tenant_id="tenant-A", today=TODAY, runtime=runtime,
        coordinator_id="coord-A", environment_id="env-A", **kw,
    )


def _rag_a():
    return FakeRag({"tenant-A": [{"ref": "doc:1", "snippet": "Acme renewed for $50k in Q1."}]})


@pytest.mark.integration
def test_live_knowledge_answer_carries_grounded_citations():
    rt = StubManagedRuntime({"answer": "Acme renewed in Q1 and looks healthy.",
                             "delegations": ["pip"], "pending_approvals": []})
    rag = _rag_a()
    convo = _convo(rt, rag=rag, synthesizer=FakeSynth())

    turn = convo.send("How is the Acme account doing?")

    # Retrieval was tenant-scoped (THE TRUST RULE rides the rag client's tenant_id kwarg).
    assert rag.calls == [("tenant-A", "How is the Acme account doing?")]
    # The coordinator's prose stands; the grounded citations attach to it.
    assert turn.answer == "Acme renewed in Q1 and looks healthy."
    assert turn.citations, "live knowledge answers must carry citations (PgRagClient results)"
    # THE INVARIANT: every citation's source_ref exists in the retrieved set...
    assert all(c["source_ref"] == "doc:1" for c in turn.citations)
    # ...and the hallucinated claim was dropped, never surfaced as grounded.
    assert all("secretly about to churn" not in c["claim"] for c in turn.citations)
    assert turn.delegations == ["pip"]


@pytest.mark.integration
def test_live_path_grounded_answer_stands_in_when_coordinator_is_silent():
    rt = StubManagedRuntime({"answer": "", "delegations": [], "pending_approvals": []})
    convo = _convo(rt, rag=_rag_a(), synthesizer=FakeSynth())

    turn = convo.send("How is the Acme account doing?")

    assert turn.citations
    assert "Acme renewed for $50k in Q1." in turn.answer
    assert "secretly about to churn" not in turn.answer  # uncited claim never reaches the prose


@pytest.mark.integration
def test_action_turns_skip_retrieval_entirely():
    rt = StubManagedRuntime({
        "answer": "",
        "delegations": ["nadia"],
        "pending_approvals": [{
            "status": "pending", "tool": "send_email",
            "input": {"to": "lead@acme.com", "subject": "hi", "body": "draft"},
            "custom_tool_use_id": "ctu_1",
        }],
    })
    rag = _rag_a()
    convo = _convo(rt, rag=rag, synthesizer=FakeSynth())

    turn = convo.send("what should we do about the Acme lead?")

    assert rag.calls == []  # no needless vector search + synthesizer call on approval turns
    assert turn.citations == []
    assert turn.pending_approvals
    assert turn.answer == "Prepared an action for your approval."


@pytest.mark.integration
def test_live_path_without_rag_client_is_unchanged():
    rt = StubManagedRuntime({"answer": "Here's the summary.", "delegations": [],
                             "pending_approvals": []})
    convo = _convo(rt)  # no rag wired

    turn = convo.send("How is the Acme account doing?")

    assert turn.answer == "Here's the summary."
    assert turn.citations == []


@pytest.mark.integration
def test_fake_runtime_citation_invariant_still_holds():
    """The offline facade path is untouched: same grounded citations, hallucination dropped."""
    rag = _rag_a()
    convo = Conversation(
        tenant_id="tenant-A", today=TODAY, runtime=get_runtime({"runtime": "fake"}),
        rag=rag, synthesizer=FakeSynth(),
    )

    turn = convo.send("How is the Acme account doing?")

    assert turn.citations
    assert all(c["source_ref"] == "doc:1" for c in turn.citations)
    assert "secretly about to churn" not in turn.answer
