"""Unit: batch_embed — the async Titan V2 Bedrock batch job (S3 JSONL in/out).

Fully mocked bedrock/s3 clients. Proves:
  * the input JSONL records carry {"recordId", "modelInput": <exact Titan body>}
  * create_model_invocation_job is shaped right (modelId/roleArn/S3 URIs)
  * the poll loop runs InProgress -> Completed, then output vectors come back
    joined by recordId IN INPUT ORDER, dim-checked
  * terminal failure statuses raise; a poll timeout raises
  * unconfigured (no bucket/role) falls back to synchronous per-text embed
"""
import hashlib
import json

import pytest

from ingest import EMBEDDING_DIM, EMBEDDING_MODEL_ID
from ingest.embed import batch_embed, build_titan_body
from shared.config import ENV_BEDROCK_BATCH_ROLE_ARN, ENV_INGEST_BATCH_S3_BUCKET

BUCKET = "uplift-batch"
ROLE = "arn:aws:iam::123456789012:role/uplift-bedrock-batch"


def _vec_for(text: str) -> list[float]:
    seed = int(hashlib.sha256(text.encode()).hexdigest(), 16)
    return [((seed >> (i % 64)) & 0xFF) / 255.0 for i in range(EMBEDDING_DIM)]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(ENV_INGEST_BATCH_S3_BUCKET, raising=False)
    monkeypatch.delenv(ENV_BEDROCK_BATCH_ROLE_ARN, raising=False)


# --------------------------------------------------------------------------- fakes
class FakeS3:
    """Records put_object; after the job 'completes', serves the output JSONL."""

    def __init__(self):
        self.puts = []
        self.objects = {}  # key -> bytes (the job output, planted by the test/bedrock fake)

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.puts.append({"Bucket": Bucket, "Key": Key, "Body": Body})

    def list_objects_v2(self, *, Bucket, Prefix, **kwargs):
        keys = [k for k in self.objects if k.startswith(Prefix)]
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}

    def get_object(self, *, Bucket, Key):
        return {"Body": self.objects[Key]}


class FakeBedrockBatch:
    """Records the create call; serves a status sequence; on Completed, writes
    the output JSONL into the fake S3 (echoing the deterministic test vectors)."""

    def __init__(self, s3: FakeS3, statuses=("InProgress", "Completed"),
                 drop_record_ids=()):
        self.s3 = s3
        self.create_calls = []
        self.statuses = list(statuses)
        self.polls = 0
        self.drop_record_ids = set(drop_record_ids)

    def create_model_invocation_job(self, **kwargs):
        self.create_calls.append(kwargs)
        return {"jobArn": "arn:aws:bedrock:us-east-1:123456789012:model-invocation-job/abc123"}

    def get_model_invocation_job(self, *, jobIdentifier):
        self.polls += 1
        status = self.statuses[min(self.polls - 1, len(self.statuses) - 1)]
        if status == "Completed" and self.create_calls:
            self._write_output()
        return {"status": status, "message": f"status={status}"}

    def _write_output(self):
        create = self.create_calls[0]
        input_uri = create["inputDataConfig"]["s3InputDataConfig"]["s3Uri"]
        output_uri = create["outputDataConfig"]["s3OutputDataConfig"]["s3Uri"]
        out_prefix = output_uri.split(f"s3://{BUCKET}/", 1)[1]
        # find the input JSONL the code uploaded, embed each record
        in_key = input_uri.split(f"s3://{BUCKET}/", 1)[1]
        put = next(p for p in self.s3.puts if p["Key"] == in_key)
        out_lines = []
        for line in put["Body"].decode("utf-8").splitlines():
            rec = json.loads(line)
            if rec["recordId"] in self.drop_record_ids:
                continue
            out_lines.append(json.dumps({
                "recordId": rec["recordId"],
                "modelOutput": {"embedding": _vec_for(rec["modelInput"]["inputText"])},
            }))
        # Bedrock writes <output-prefix>/<job-id>/<input-name>.out — emulate that layout.
        self.s3.objects[f"{out_prefix}job-abc123/{in_key.rsplit('/', 1)[-1]}.out"] = (
            "\n".join(out_lines).encode("utf-8")
        )


def _run(texts, record_ids, **kwargs):
    s3 = FakeS3()
    bedrock = FakeBedrockBatch(s3, **kwargs.pop("bedrock_kwargs", {}))
    vecs = batch_embed(
        texts, record_ids=record_ids, s3_bucket=BUCKET, role_arn=ROLE,
        bedrock_client=bedrock, s3_client=s3, poll_interval_s=0, job_name="testjob",
        **kwargs,
    )
    return vecs, s3, bedrock


# --------------------------------------------------------------------------- happy path
@pytest.mark.unit
def test_batch_job_shapes_input_jsonl_and_create_call():
    texts = ["alpha", "beta", "gamma"]
    rids = ["doc-1", "doc-2", "doc-3"]
    vecs, s3, bedrock = _run(texts, rids)

    # 1) input JSONL: one record per text, exact Titan body under modelInput
    assert len(s3.puts) == 1
    put = s3.puts[0]
    assert put["Bucket"] == BUCKET
    assert put["Key"] == "batch-embed/input/testjob.jsonl"
    lines = [json.loads(ln) for ln in put["Body"].decode("utf-8").splitlines()]
    assert [ln["recordId"] for ln in lines] == rids
    for line, text in zip(lines, texts):
        assert line["modelInput"] == build_titan_body(text)
        assert line["modelInput"]["dimensions"] == 1024
        assert line["modelInput"]["normalize"] is True

    # 2) job creation: Titan model, the role, and the exact S3 URIs
    assert len(bedrock.create_calls) == 1
    call = bedrock.create_calls[0]
    assert call["modelId"] == EMBEDDING_MODEL_ID
    assert call["roleArn"] == ROLE
    assert call["jobName"] == "testjob"
    assert call["inputDataConfig"]["s3InputDataConfig"]["s3Uri"] == (
        f"s3://{BUCKET}/batch-embed/input/testjob.jsonl"
    )
    assert call["outputDataConfig"]["s3OutputDataConfig"]["s3Uri"] == (
        f"s3://{BUCKET}/batch-embed/output/testjob/"
    )

    # 3) polled to completion, vectors joined by recordId in INPUT order
    assert bedrock.polls == 2  # InProgress, then Completed
    assert len(vecs) == 3
    assert vecs == [_vec_for(t) for t in texts]
    assert all(len(v) == EMBEDDING_DIM for v in vecs)


@pytest.mark.unit
def test_batch_resolves_bucket_and_role_from_env(monkeypatch):
    monkeypatch.setenv(ENV_INGEST_BATCH_S3_BUCKET, BUCKET)
    monkeypatch.setenv(ENV_BEDROCK_BATCH_ROLE_ARN, ROLE)
    s3 = FakeS3()
    bedrock = FakeBedrockBatch(s3)
    vecs = batch_embed(["x"], record_ids=["r1"], bedrock_client=bedrock,
                       s3_client=s3, poll_interval_s=0, job_name="envjob")
    assert bedrock.create_calls[0]["roleArn"] == ROLE
    assert vecs == [_vec_for("x")]


# --------------------------------------------------------------------------- failure modes
@pytest.mark.unit
@pytest.mark.parametrize("status", ["Failed", "Stopped", "Expired", "PartiallyCompleted"])
def test_terminal_failure_status_raises(status):
    with pytest.raises(RuntimeError, match=status):
        _run(["a"], ["r1"], bedrock_kwargs={"statuses": ("InProgress", status)})


@pytest.mark.unit
def test_poll_timeout_raises():
    with pytest.raises(TimeoutError):
        _run(["a"], ["r1"], timeout_s=0,
             bedrock_kwargs={"statuses": ("InProgress",)})


@pytest.mark.unit
def test_missing_record_in_output_raises():
    with pytest.raises(RuntimeError, match="missing"):
        _run(["a", "b"], ["r1", "r2"], bedrock_kwargs={"drop_record_ids": {"r2"}})


@pytest.mark.unit
def test_record_ids_must_match_and_be_unique():
    with pytest.raises(ValueError, match="1:1"):
        batch_embed(["a", "b"], record_ids=["r1"], s3_bucket=BUCKET, role_arn=ROLE,
                    bedrock_client=object(), s3_client=object())
    with pytest.raises(ValueError, match="unique"):
        batch_embed(["a", "b"], record_ids=["r1", "r1"], s3_bucket=BUCKET, role_arn=ROLE,
                    bedrock_client=object(), s3_client=object())


# --------------------------------------------------------------------------- fallback
class FakeRuntimeClient:
    """bedrock-runtime fake for the synchronous fallback path."""

    def __init__(self):
        self.calls = 0

    def invoke_model(self, *, modelId, body, **kwargs):
        self.calls += 1
        text = json.loads(body)["inputText"]
        return {"embedding": _vec_for(text)}


@pytest.mark.unit
def test_unconfigured_falls_back_to_sync_embed():
    runtime = FakeRuntimeClient()
    vecs = batch_embed(["a", "b"], client=runtime)  # no bucket/role anywhere
    assert runtime.calls == 2
    assert vecs == [_vec_for("a"), _vec_for("b")]


@pytest.mark.unit
def test_partially_configured_still_falls_back(monkeypatch):
    monkeypatch.setenv(ENV_INGEST_BATCH_S3_BUCKET, BUCKET)  # bucket but NO role
    runtime = FakeRuntimeClient()
    vecs = batch_embed(["a"], client=runtime)
    assert runtime.calls == 1
    assert len(vecs) == 1


@pytest.mark.unit
def test_empty_texts_short_circuits():
    assert batch_embed([]) == []
