"""Embedding via Amazon Bedrock Titan Text Embeddings V2 (1024 dims, locked).

`embed(text, client=None)` shapes the exact Bedrock InvokeModel call:
    modelId = "amazon.titan-embed-text-v2:0"
    body    = {"inputText": <text>, "dimensions": 1024, "normalize": true}
and parses the 1024-float vector out of the response.

The client is INJECTED. Tests pass a fake returning a deterministic 1024-vector.
The real boto3 client is built LAZILY (only inside _default_client, only when
embed() is actually called with no client) so importing this module never needs
AWS, boto3, or credentials.

The dimensionality is locked to match db/schema.sql `documents.embedding vector(1024)`
— changing it forces a full re-embed.
"""
from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from . import EMBEDDING_DIM, EMBEDDING_MODEL_ID

# Bedrock request body keys — kept as constants so tests/readers can assert shape.
INPUT_TEXT_KEY = "inputText"
DIMENSIONS_KEY = "dimensions"
NORMALIZE_KEY = "normalize"


@runtime_checkable
class BedrockClient(Protocol):
    """The subset of the boto3 bedrock-runtime client we depend on."""

    def invoke_model(self, *, modelId: str, body: str, **kwargs: Any) -> Any: ...


def build_titan_body(text: str) -> dict:
    """Return the Titan V2 request body dict (1024 dims, normalized)."""
    return {
        INPUT_TEXT_KEY: text,
        DIMENSIONS_KEY: EMBEDDING_DIM,
        NORMALIZE_KEY: True,
    }


def _default_client() -> BedrockClient:
    """Lazily construct the real Bedrock runtime client.

    Imported INSIDE the function so module import never touches boto3/AWS.
    """
    import os

    import boto3  # noqa: PLC0415 — lazy on purpose

    region = os.environ.get("AWS_REGION", "us-east-1")
    return boto3.client("bedrock-runtime", region_name=region)


def _parse_embedding(payload: dict) -> list[float]:
    """Pull the embedding vector out of a Titan response payload."""
    vec = payload.get("embedding")
    if vec is None:
        raise ValueError("Titan response missing 'embedding'")
    if len(vec) != EMBEDDING_DIM:
        raise ValueError(
            f"embedding dim {len(vec)} != expected {EMBEDDING_DIM} "
            f"(must match documents.embedding vector({EMBEDDING_DIM}))"
        )
    return [float(x) for x in vec]


def embed(text: str, client: BedrockClient | None = None) -> list[float]:
    """Embed `text` → a 1024-float vector via Titan V2.

    Pass a fake `client` in tests. With client=None the real Bedrock client is
    built lazily (requires AWS at call time only, never at import).
    """
    if client is None:
        client = _default_client()

    body = build_titan_body(text)
    resp = client.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        body=json.dumps(body),
        accept="application/json",
        contentType="application/json",
    )

    # boto3 returns {"body": StreamingBody}; fakes may return a dict directly.
    payload = _read_response_payload(resp)
    return _parse_embedding(payload)


def _read_response_payload(resp: Any) -> dict:
    """Normalize a Bedrock invoke_model response into a dict payload.

    Handles: boto3 ({"body": stream-with-.read()}), a fake returning {"body": str},
    or a fake returning the parsed payload dict directly.
    """
    if isinstance(resp, dict) and "embedding" in resp:
        return resp
    body = resp["body"] if isinstance(resp, dict) and "body" in resp else resp
    if hasattr(body, "read"):
        body = body.read()
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8")
    if isinstance(body, str):
        return json.loads(body)
    if isinstance(body, dict):
        return body
    raise TypeError(f"unrecognized Bedrock response type: {type(body)!r}")


def batch_embed(
    texts: list[str],
    *,
    s3_input_uri: str | None = None,
    s3_output_uri: str | None = None,
    client: BedrockClient | None = None,
) -> list[list[float]]:
    """STUB — documents the Bedrock Batch (S3-JSONL) path; not a real job here.

    The production design for large backfills:
      1. Write one JSON-Lines record per text to `s3_input_uri`:
            {"recordId": <ref_id>,
             "modelInput": {"inputText": ..., "dimensions": 1024, "normalize": true}}
      2. Start an async embeddings job via
            bedrock.create_model_invocation_job(
                modelId="amazon.titan-embed-text-v2:0",
                inputDataConfig={...s3_input_uri...},
                outputDataConfig={...s3_output_uri...})
      3. Poll get_model_invocation_job until Completed; read 1024-vectors back
         from the S3 JSONL output, joined to documents by recordId == ref_id.

    Batch is ~50% the per-token cost of on-demand and is the right path for the
    initial full sync; incremental syncs use the synchronous `embed()` above.

    Here (offline) we fall back to per-text synchronous `embed()` so callers and
    tests have a working surface without launching a real AWS Batch job.
    VERIFY: real Batch job wiring (create/poll/parse) is unimplemented by design.
    """
    return [embed(t, client=client) for t in texts]
