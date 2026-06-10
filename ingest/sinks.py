"""Real sink implementations for the ingestion pipeline.

S3RawSink — the production RawSink (the raw lake): untouched source JSON, keyed
for replay as  {prefix}/{tenant_id}/{source}/{ref_id}.json .

IMPORT SAFETY: boto3 is imported lazily on the first put when no client was
injected — importing this module never needs AWS. Tests inject a fake `client`
with `put_object`.

The structured (Aurora) sink is deliberately NOT here yet: the connectors'
normalized rows still carry source-ref columns (company_ref_id / source) that
db/schema.sql's CRM tables don't have, so landing them needs a ref->uuid
resolution pass first (TODO follow-up). run_sync keeps the in-memory structured
sink and says so loudly.
"""
from __future__ import annotations

import json
from typing import Any


class S3RawSink:
    """RawSink over S3 (lazy boto3; injected fake client in tests)."""

    def __init__(self, bucket: str, *, prefix: str = "raw",
                 region: str | None = None, client: Any = None) -> None:
        if not bucket:
            raise ValueError("S3RawSink requires a bucket name")
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._region = region
        self._client = client

    def _s3(self) -> Any:
        if self._client is None:
            import os  # noqa: PLC0415 — lazy with boto3 below

            import boto3  # noqa: PLC0415 — lazy: import-safe module

            region = self._region or os.environ.get("AWS_REGION", "us-east-1")
            self._client = boto3.client("s3", region_name=region)
        return self._client

    def put_raw(self, tenant_id: str, source: str, ref_id: str, record: dict) -> str:
        key = f"{self._prefix}/{tenant_id}/{source}/{ref_id}.json"
        self._s3().put_object(
            Bucket=self._bucket,
            Key=key,
            Body=json.dumps(record, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        return f"s3://{self._bucket}/{key}"
