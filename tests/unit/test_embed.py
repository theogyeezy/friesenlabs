"""Unit: embed() shapes the Titan V2 request and returns a 1024-vector (fake client)."""
import hashlib
import json

import pytest

from ingest import EMBEDDING_DIM, EMBEDDING_MODEL_ID
from ingest.embed import batch_embed, build_titan_body, embed


class FakeBedrockClient:
    """Records the invoke_model call and returns a deterministic 1024-vector."""

    def __init__(self, response_style="stream"):
        self.calls = []
        self.response_style = response_style

    def invoke_model(self, *, modelId, body, **kwargs):
        self.calls.append({"modelId": modelId, "body": body, "kwargs": kwargs})
        parsed = json.loads(body)
        # Deterministic vector seeded from the input text.
        seed = int(hashlib.sha256(parsed["inputText"].encode()).hexdigest(), 16)
        vec = [((seed >> (i % 64)) & 0xFF) / 255.0 for i in range(EMBEDDING_DIM)]
        payload = {"embedding": vec}
        if self.response_style == "stream":
            class _Body:
                def __init__(self, data):
                    self._data = data

                def read(self):
                    return json.dumps(self._data).encode("utf-8")

            return {"body": _Body(payload)}
        if self.response_style == "str_body":
            return {"body": json.dumps(payload)}
        return payload  # direct dict


@pytest.mark.unit
def test_build_titan_body_shape():
    body = build_titan_body("hello")
    assert body == {"inputText": "hello", "dimensions": 1024, "normalize": True}


@pytest.mark.unit
def test_embed_returns_1024_vector_and_shapes_request():
    client = FakeBedrockClient()
    vec = embed("renewal call notes", client=client)

    assert isinstance(vec, list)
    assert len(vec) == EMBEDDING_DIM == 1024
    assert all(isinstance(x, float) for x in vec)

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["modelId"] == EMBEDDING_MODEL_ID == "amazon.titan-embed-text-v2:0"
    sent = json.loads(call["body"])
    assert sent["dimensions"] == 1024
    assert sent["normalize"] is True
    assert sent["inputText"] == "renewal call notes"


@pytest.mark.unit
def test_embed_is_deterministic_for_same_text():
    client = FakeBedrockClient()
    assert embed("abc", client=client) == embed("abc", client=client)


@pytest.mark.unit
@pytest.mark.parametrize("style", ["stream", "str_body", "dict"])
def test_embed_handles_response_shapes(style):
    client = FakeBedrockClient(response_style=style)
    vec = embed("x", client=client)
    assert len(vec) == 1024


@pytest.mark.unit
def test_embed_rejects_wrong_dim():
    class BadClient:
        def invoke_model(self, *, modelId, body, **kwargs):
            return {"embedding": [0.0] * 512}

    with pytest.raises(ValueError):
        embed("x", client=BadClient())


@pytest.mark.unit
def test_batch_embed_falls_back_to_sync_embed_offline():
    client = FakeBedrockClient()
    vecs = batch_embed(["a", "b", "c"], client=client)
    assert len(vecs) == 3
    assert all(len(v) == 1024 for v in vecs)
