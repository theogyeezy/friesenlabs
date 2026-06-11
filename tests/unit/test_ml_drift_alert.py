"""Unit: Cortex drift alerting (ml/drift_alert.py) — fake SNS, no boto3, no network."""
import json

import pytest

from ml.drift_alert import DriftNotifier, from_env


class FakeSns:
    def __init__(self):
        self.published = []

    def publish(self, **kw):
        self.published.append(kw)
        return {"MessageId": "m1"}


DRIFTED = {"drift": True, "registered_auc": 0.81, "recent_auc": 0.62, "n_outcomes": 40, "reason": "degraded beyond tolerance"}
OK = {"drift": False, "registered_auc": 0.81, "recent_auc": 0.80, "n_outcomes": 40, "reason": "ok"}
NO_EVIDENCE = {"drift": False, "recent_auc": None, "n_outcomes": 3, "reason": "insufficient live evidence"}


@pytest.mark.unit
def test_notify_publishes_on_drift():
    sns = FakeSns()
    sent = DriftNotifier("arn:topic", sns=sns).notify("T", DRIFTED)
    assert sent is True
    assert len(sns.published) == 1
    msg = sns.published[0]
    assert msg["TopicArn"] == "arn:topic"
    assert "tenant T" in msg["Subject"]
    body = json.loads(msg["Message"])
    assert body["tenant_id"] == "T" and body["recent_auc"] == 0.62


@pytest.mark.unit
def test_notify_noop_when_not_drift():
    sns = FakeSns()
    assert DriftNotifier("arn:topic", sns=sns).notify("T", OK) is False
    assert DriftNotifier("arn:topic", sns=sns).notify("T", NO_EVIDENCE) is False
    assert DriftNotifier("arn:topic", sns=sns).notify("T", {}) is False
    assert sns.published == []  # we never page on "fine"/"no evidence"


@pytest.mark.unit
def test_subject_truncated_to_sns_limit():
    sns = FakeSns()
    DriftNotifier("arn:topic", sns=sns).notify("x" * 200, DRIFTED)
    assert len(sns.published[0]["Subject"]) <= 100


@pytest.mark.unit
def test_from_env_inert_without_arn():
    assert from_env({}) is None
    assert from_env({"CORTEX_DRIFT_TOPIC_ARN": "  "}) is None


@pytest.mark.unit
def test_from_env_builds_with_arn():
    n = from_env({"CORTEX_DRIFT_TOPIC_ARN": "arn:topic", "AWS_REGION": "us-east-1"})
    assert isinstance(n, DriftNotifier)
