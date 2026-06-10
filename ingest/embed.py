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


# --------------------------------------------------------------------------- #
# Bedrock BATCH embeddings (async model-invocation job) — the backfill path.
# Batch is ~50% the per-token cost of on-demand and is the right path for the
# initial full sync; incremental syncs keep using the synchronous embed() above.
# --------------------------------------------------------------------------- #
@runtime_checkable
class BedrockBatchClient(Protocol):
    """The subset of the boto3 `bedrock` (control-plane, NOT bedrock-runtime)
    client the batch path depends on."""

    def create_model_invocation_job(self, **kwargs: Any) -> dict: ...
    def get_model_invocation_job(self, *, jobIdentifier: str) -> dict: ...


# Statuses that end the poll loop without success.
# VERIFY: status vocabulary against the live get_model_invocation_job response —
# PartiallyCompleted is treated as failure here (some records would be missing).
BATCH_FAILURE_STATUSES = ("Failed", "Stopped", "Expired", "PartiallyCompleted")


def _default_batch_clients() -> tuple[Any, Any]:
    """Lazily build the real (bedrock control-plane, s3) boto3 clients."""
    import os  # noqa: PLC0415 — lazy with boto3 below

    import boto3  # noqa: PLC0415 — lazy on purpose (import-safe module)

    region = os.environ.get("AWS_REGION", "us-east-1")
    return boto3.client("bedrock", region_name=region), boto3.client("s3", region_name=region)


def _parse_batch_output(s3_client: Any, bucket: str, output_prefix: str,
                        record_ids: list[str]) -> list[list[float]]:
    """Read the job's JSONL output object(s) under `output_prefix` and return the
    1024-vectors in input order, joined on recordId.

    # VERIFY: Bedrock writes output to s3://<bucket>/<prefix>/<jobId>/<input>.jsonl.out —
    # we list everything under the job's output prefix and parse any *.jsonl.out
    # (falling back to any *.out / *.jsonl) so the exact layout can shift without
    # breaking the join; each line is {"recordId": ..., "modelOutput": {"embedding": [...]}}.
    """
    keys: list[str] = []
    token: str | None = None
    while True:
        kwargs: dict = {"Bucket": bucket, "Prefix": output_prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3_client.list_objects_v2(**kwargs)
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
    out_keys = [k for k in keys if k.endswith(".jsonl.out")] or [
        k for k in keys if k.endswith((".out", ".jsonl"))
    ]
    if not out_keys:
        raise RuntimeError(f"batch embed: no output objects under s3://{bucket}/{output_prefix}")

    by_id: dict[str, list[float]] = {}
    for key in out_keys:
        body = s3_client.get_object(Bucket=bucket, Key=key)["Body"]
        data = body.read() if hasattr(body, "read") else body
        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8")
        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            model_output = rec.get("modelOutput") or {}
            if "embedding" not in model_output:
                # Bedrock emits error records without modelOutput; surface the first one.
                raise RuntimeError(
                    f"batch embed: record {rec.get('recordId')!r} has no embedding "
                    f"(error: {rec.get('error')!r})"
                )
            by_id[str(rec.get("recordId"))] = _parse_embedding(model_output)
    missing = [rid for rid in record_ids if rid not in by_id]
    if missing:
        raise RuntimeError(f"batch embed: {len(missing)} record(s) missing from output "
                           f"(first: {missing[0]!r})")
    return [by_id[rid] for rid in record_ids]


def batch_embed(
    texts: list[str],
    *,
    record_ids: list[str] | None = None,
    s3_bucket: str | None = None,
    role_arn: str | None = None,
    s3_prefix: str = "batch-embed",
    job_name: str | None = None,
    bedrock_client: BedrockBatchClient | None = None,
    s3_client: Any = None,
    poll_interval_s: float = 30.0,
    timeout_s: float = 4 * 3600.0,
    client: BedrockClient | None = None,
) -> list[list[float]]:
    """Embed `texts` via an async Titan V2 Bedrock BATCH job (S3 JSONL in/out).

    Flow (the production backfill path):
      1. Write one JSON-Lines record per text to
         s3://{bucket}/{s3_prefix}/input/{job}.jsonl:
            {"recordId": <id>, "modelInput": {"inputText": ..., "dimensions": 1024,
                                              "normalize": true}}
      2. bedrock.create_model_invocation_job(modelId=Titan V2, roleArn=...,
         inputDataConfig/outputDataConfig pointing at those S3 URIs).
      3. Poll get_model_invocation_job until Completed (or fail on a terminal
         failure status / `timeout_s`).
      4. Parse the output JSONL, join vectors back by recordId, return them in
         input order (every vector dim-checked against EMBEDDING_DIM).

    Configuration: `s3_bucket`/`role_arn` come from the arguments or, when None,
    from the INGEST_BATCH_S3_BUCKET / BEDROCK_BATCH_ROLE_ARN env vars
    (shared/config.py names). When EITHER is missing the function falls back to
    synchronous per-text `embed()` — the safe incremental-sync behavior, and what
    keeps tests/offline runs working with no AWS.

    boto3 clients are built lazily only when the batch path actually runs and no
    fakes were injected (`bedrock_client`/`s3_client`); `client` is the
    bedrock-runtime client used only by the synchronous fallback.

    # VERIFY: the create_model_invocation_job kwarg/response shapes below are
    # authored from the documented boto3 'bedrock' API and are NOT live-verified.
    """
    if not texts:
        return []
    if record_ids is None:
        record_ids = [f"rec-{i:08d}" for i in range(len(texts))]
    if len(record_ids) != len(texts):
        raise ValueError("record_ids must match texts 1:1")
    if len(set(record_ids)) != len(record_ids):
        raise ValueError("record_ids must be unique (they join the output back)")

    import os  # noqa: PLC0415 — keep module import side-effect free

    from shared.config import (  # noqa: PLC0415 — env NAMES live in shared/config.py
        ENV_BEDROCK_BATCH_ROLE_ARN,
        ENV_INGEST_BATCH_S3_BUCKET,
    )

    bucket = s3_bucket if s3_bucket is not None else os.environ.get(ENV_INGEST_BATCH_S3_BUCKET, "")
    role = role_arn if role_arn is not None else os.environ.get(ENV_BEDROCK_BATCH_ROLE_ARN, "")
    if not bucket or not role:
        # Unconfigured -> synchronous fallback (offline/test-safe; no AWS).
        return [embed(t, client=client) for t in texts]

    import time  # noqa: PLC0415
    import uuid  # noqa: PLC0415

    if bedrock_client is None or s3_client is None:
        default_bedrock, default_s3 = _default_batch_clients()
        bedrock_client = bedrock_client or default_bedrock
        s3_client = s3_client or default_s3

    job = job_name or f"uplift-batch-embed-{uuid.uuid4().hex[:12]}"
    prefix = s3_prefix.strip("/")
    input_key = f"{prefix}/input/{job}.jsonl"
    output_prefix = f"{prefix}/output/{job}/"

    lines = [
        json.dumps({"recordId": rid, "modelInput": build_titan_body(text)})
        for rid, text in zip(record_ids, texts)
    ]
    s3_client.put_object(
        Bucket=bucket, Key=input_key, Body=("\n".join(lines) + "\n").encode("utf-8")
    )

    resp = bedrock_client.create_model_invocation_job(
        jobName=job,
        modelId=EMBEDDING_MODEL_ID,
        roleArn=role,
        inputDataConfig={"s3InputDataConfig": {"s3Uri": f"s3://{bucket}/{input_key}"}},
        outputDataConfig={"s3OutputDataConfig": {"s3Uri": f"s3://{bucket}/{output_prefix}"}},
    )
    job_id = resp.get("jobArn") or resp.get("jobIdentifier") or job  # VERIFY: response key

    deadline = time.monotonic() + timeout_s
    while True:
        status_resp = bedrock_client.get_model_invocation_job(jobIdentifier=job_id)
        status = status_resp.get("status")
        if status == "Completed":
            break
        if status in BATCH_FAILURE_STATUSES:
            raise RuntimeError(
                f"batch embed job {job!r} ended {status}: {status_resp.get('message', '')!r}"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(f"batch embed job {job!r} did not complete in {timeout_s}s "
                               f"(last status: {status!r})")
        time.sleep(poll_interval_s)

    return _parse_batch_output(s3_client, bucket, output_prefix, record_ids)
