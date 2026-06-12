"""Unit: honest degraded-path logging — a swallowed failure must leave a real trace in the logs.

Two degrade paths previously hid their cause:
  * api/knowledge_routes.py: /knowledge/search caught every embed/model error and logged ONLY the
    exception TYPE — an operator couldn't tell a missing Bedrock key from a model error from a DB
    outage. The wire response stays generic (search_available:false); the SERVER log now carries the
    real reason + traceback.
  * conv/synthesizer.py: a live-model failure (or unusable output) fell back to extractive synthesis
    SILENTLY. It now logs the real reason, so a degraded synthesizer is visible, not invisible behind
    a seemingly-fine answer.

No fabrication: the wire/answer behavior is unchanged — only the logs gained honest detail.
"""
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import TenantClaims
from api.knowledge_routes import KnowledgeDeps, mount_knowledge
from conv.synthesizer import AnthropicSynthesizer


# --------------------------------------------------------------------------- knowledge
def _current_tenant() -> TenantClaims:
    return TenantClaims(tenant_id="T1", sub="u1", email="a@x.com")


class _ExplodingRag:
    def search(self, *, tenant_id, query, limit=8, offset=0):
        raise RuntimeError("Titan embed model not reachable: AccessDeniedException")


@pytest.mark.unit
def test_knowledge_search_logs_the_real_reason_not_just_the_type(caplog):
    app = FastAPI()
    mount_knowledge(app, KnowledgeDeps(rag=_ExplodingRag()), _current_tenant)
    client = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="api.knowledge"):
        resp = client.get("/knowledge/search", params={"q": "renewals"})
    # Wire response stays honest + generic (no leaked AWS string).
    assert resp.status_code == 200
    body = resp.json()
    assert body["search_available"] is False and body["results"] == []
    # Server log carries the REAL reason (the exception message), not only the type name.
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "Titan embed model not reachable" in logged
    assert "RuntimeError" in logged


# --------------------------------------------------------------------------- synthesizer
class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _Messages:
    def __init__(self, *, payload=None, error=None):
        self._payload = payload
        self._error = error

    def create(self, **kwargs):
        if self._error is not None:
            raise self._error
        return _Resp(self._payload)


class _FakeClient:
    def __init__(self, *, payload=None, error=None):
        self.messages = _Messages(payload=payload, error=error)


CHUNKS = [{"ref": "doc:1", "snippet": "Acme renewed for $50k.", "source": "rag"}]


@pytest.mark.unit
def test_synthesizer_logs_when_model_call_fails(caplog):
    synth = AnthropicSynthesizer(client=_FakeClient(error=RuntimeError("API 529 overloaded")))
    with caplog.at_level(logging.WARNING, logger="conv.synthesizer"):
        out = synth.synthesize(question="how did Acme do?", chunks=CHUNKS)
    # Degrades to the extractive fallback (never crashes) — a real, grounded answer shape.
    assert isinstance(out["claims"], list) and out["claims"]
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "live model call failed" in logged


@pytest.mark.unit
def test_synthesizer_logs_when_model_output_is_unusable(caplog):
    synth = AnthropicSynthesizer(client=_FakeClient(payload="not json at all"))
    with caplog.at_level(logging.INFO, logger="conv.synthesizer"):
        out = synth.synthesize(question="how did Acme do?", chunks=CHUNKS)
    # Unparseable output -> extractive fallback (grounded), and an honest "unusable" log line.
    assert isinstance(out["claims"], list) and out["claims"]
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "unusable" in logged


@pytest.mark.unit
def test_synthesizer_clean_path_does_not_log_a_failure(caplog):
    # A good generation must NOT emit any degrade log (no false alarms).
    import json
    good = json.dumps({"claims": [{"text": "Acme renewed for $50k.", "source_refs": ["doc:1"]}]})
    synth = AnthropicSynthesizer(client=_FakeClient(payload=good))
    with caplog.at_level(logging.INFO, logger="conv.synthesizer"):
        out = synth.synthesize(question="how did Acme do?", chunks=CHUNKS)
    assert out["claims"] == [{"text": "Acme renewed for $50k.", "source_refs": ["doc:1"]}]
    assert not caplog.records
