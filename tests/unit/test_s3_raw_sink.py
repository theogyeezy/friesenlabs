"""Unit: S3RawSink — the production raw lake (mocked boto3 S3 client)."""
import json

import pytest

from ingest.sinks import S3RawSink


class FakeS3:
    def __init__(self):
        self.puts = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)


@pytest.mark.unit
def test_put_raw_keys_and_body():
    s3 = FakeS3()
    sink = S3RawSink("raw-bucket", client=s3)
    uri = sink.put_raw("t1", "hubspot", "ct-9", {"id": "ct-9", "properties": {"a": 1}})

    assert uri == "s3://raw-bucket/raw/t1/hubspot/ct-9.json"
    assert len(s3.puts) == 1
    put = s3.puts[0]
    assert put["Bucket"] == "raw-bucket"
    assert put["Key"] == "raw/t1/hubspot/ct-9.json"
    assert json.loads(put["Body"].decode("utf-8")) == {"id": "ct-9", "properties": {"a": 1}}
    assert put["ContentType"] == "application/json"


@pytest.mark.unit
def test_custom_prefix_and_required_bucket():
    s3 = FakeS3()
    sink = S3RawSink("b", prefix="/lake/", client=s3)
    assert sink.put_raw("t", "s", "r", {}) == "s3://b/lake/t/s/r.json"
    with pytest.raises(ValueError):
        S3RawSink("")
