"""Integration: the Conversation facade over FakeRuntime (Build Guide Step 38).

A knowledge question returns a cited answer for the right tenant. An action question surfaces a
pending approval — reusing the Phase 4 side-effecting tools + the Phase 5 Greenlight queue by
injection (imported, not reimplemented). No real Anthropic/AWS.
"""
from datetime import date

import pytest

from agents.runtime import get_runtime
from api.control.greenlight import Greenlight
from conv.analytics import Analytics, EventType
from conv.session import Conversation

TODAY = date(2026, 5, 15)


# --------------------------------------------------------------------------- fakes
class FakeRag:
    def __init__(self, hits_by_tenant):
        self.hits_by_tenant = hits_by_tenant
        self.called_with = []

    def search(self, *, tenant_id, query, limit=8):
        self.called_with.append((tenant_id, query))
        return self.hits_by_tenant.get(tenant_id, [])


class FakeSynth:
    def synthesize(self, *, question, chunks):
        # Cite exactly the retrieved chunks (grounded), plus one hallucination to prove it is dropped.
        claims = [{"text": c["snippet"], "source_refs": [c["ref"]]} for c in chunks]
        claims.append({"text": "Acme is secretly about to churn.", "source_refs": ["doc:hallucinated"]})
        return {"summary": None, "claims": claims}


@pytest.mark.integration
def test_knowledge_question_returns_cited_answer_for_right_tenant():
    rag = FakeRag(
        {
            "tenant-A": [{"ref": "doc:1", "snippet": "Acme renewed for $50k in Q1."}],
            "tenant-B": [{"ref": "doc:9", "snippet": "OTHER TENANT secret data."}],
        }
    )
    analytics = Analytics()
    convo = Conversation(
        tenant_id="tenant-A",
        today=TODAY,
        runtime=get_runtime({"runtime": "fake"}),
        rag=rag,
        synthesizer=FakeSynth(),
        analytics=analytics,
    )

    turn = convo.send("How is the Acme account doing?")

    # Tenant-scoped retrieval — only tenant-A's data was searched.
    assert rag.called_with[0][0] == "tenant-A"
    assert turn.tenant_id == "tenant-A"
    # A grounded citation came back; the hallucinated claim was dropped, not cited.
    assert turn.citations
    assert all(c["source_ref"] == "doc:1" for c in turn.citations)
    assert "secretly about to churn" not in turn.answer
    # No other tenant's snippet leaked.
    assert "OTHER TENANT" not in turn.answer
    # The coordinator (FakeRuntime) recorded delegations for the trace.
    assert "scout" in turn.delegations

    # Analytics captured the utterance, tenant-scoped.
    utt = analytics.list("tenant-A", type=EventType.UTTERANCE)
    assert len(utt) == 1
    assert analytics.list("tenant-B") == []


@pytest.mark.integration
def test_action_question_surfaces_pending_approval_via_greenlight():
    gl = Greenlight()  # Phase 5 control-plane queue, injected
    analytics = Analytics()
    convo = Conversation(
        tenant_id="tenant-A",
        today=TODAY,
        runtime=get_runtime({"runtime": "fake"}),
        greenlight=gl,
        analytics=analytics,
    )

    # An action utterance -> the Phase 4 SendEmail tool -> a proposal queued in Greenlight, NOT sent.
    turn = convo.send(
        "send an email to the Acme contact",
        to="lead@acme.com",
        subject="Following up",
        body="Hi there",
    )

    assert turn.pending_approvals, "an action should surface a pending approval"
    approval = turn.pending_approvals[0]
    assert approval["status"] == "pending"
    assert approval["proposed_action"]["action"] == "send_email"

    # It landed in the real control-plane queue, tenant-scoped, and nothing was sent.
    pending = gl.list_pending("tenant-A")
    assert len(pending) == 1
    assert pending[0]["proposed_action"]["to"] == "lead@acme.com"
    assert gl.list_pending("tenant-B") == []  # isolation holds

    # Analytics recorded the tool_call and approval events.
    assert analytics.list("tenant-A", type=EventType.TOOL_CALL)
    assert analytics.list("tenant-A", type=EventType.APPROVAL)


@pytest.mark.integration
def test_conversation_requires_persisted_coordinator_on_real_runtime():
    # The per-request roster build is a clearly-gated FakeRuntime-only fallback: on any non-fake
    # runtime, a missing coordinator_id means the tenant isn't provisioned -> raise, never build.
    class NotFake:
        def create_session(self, *a, **k):  # pragma: no cover — must never be reached
            raise AssertionError("session must not be created without a persisted coordinator")

    with pytest.raises(RuntimeError, match="coordinator_id is required"):
        Conversation(tenant_id="tenant-A", today=TODAY, runtime=NotFake())


@pytest.mark.integration
def test_conversation_uses_persisted_ids_without_rebuilding_roster():
    rt = get_runtime({"runtime": "fake"})
    convo = Conversation(
        tenant_id="tenant-A", today=TODAY, runtime=rt,
        coordinator_id="coord-A", environment_id="env-A",
    )
    # Provisioning was hoisted out of the request path: nothing was (re)built on the runtime.
    assert rt.environments == [] and rt.coordinators == {}
    assert convo.session.coordinator_id == "coord-A"
    assert convo.session.metadata["environment_id"] == "env-A"  # per-tenant env binding


@pytest.mark.integration
def test_action_turn_resolves_date_slot_deterministically():
    gl = Greenlight()
    convo = Conversation(tenant_id="tenant-A", today=TODAY, greenlight=gl)
    turn = convo.send(
        "send a recap email of last quarter",
        to="x@y.com",
        body="recap",
    )
    # Slot resolution ran with the injected `today` (deterministic), not the system clock.
    assert turn.slots["date_range"] == {
        "start": "2026-01-01",
        "end": "2026-03-31",
        "phrase": turn.slots["date_range"]["phrase"],
    }
    assert turn.pending_approvals
