"""Integration: GET /workflows — the real Workflows tab (the provisioning machine, read-only).

Proves the api half of the workflows vertical slice (the test shapes mirror
test_api_agents.py):
  * 401 unauth (the shared current_tenant dependency)
  * the STATIC step diagram is the OWNED funnel — signup → verify → pay → provision →
    activate, 5 steps with descriptions — present in EVERY response shape (success,
    pending-IAM, not-configured): the tab stays useful no matter what AWS answers
  * success shape (fake sfn client): recent executions with name + status + timestamps
    ONLY, max 20 requested, datetimes ISO-serialized
  * THE STRIPPING RULE: the machine/execution ARNs and the AWS account id NEVER appear in
    the response body — the machine is named by its display name only
  * AccessDenied (the KNOWN live constraint: the api task role has states:StartExecution
    only) → HTTP 200 with executions_available: false + reason "pending IAM grant
    (REQ-009)" — an honest degrade, never a 500/403 error wall
  * other AWS failures (throttle/outage) → the same honest 200 degrade, generic reason,
    no error text leaked
  * no ARN configured (the live posture today: api_provisioning_sfn=false) → 200 with
    reason "not configured" and NO boto3 client ever touched
  * the default ApiDeps mounts the route with the honest inert stub (never 404)
  * READ-ONLY: only GET is mounted (POST/PUT/PATCH/DELETE → 405); the fake client sees
    list_executions calls ONLY — no Describe*, no Start/StopExecution ever
  * IMPORT SAFETY (the image-fileset discipline, extended to boto3): importing the route
    module — and building the whole app with default deps — must not import boto3
"""
import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import ApiDeps, create_app
from api.control.autonomy import AutonomyConfig
from api.control.greenlight import Greenlight
from api.views import SavedViews
from api.workflows_routes import (
    MAX_EXECUTIONS,
    REASON_NOT_CONFIGURED,
    REASON_PENDING_IAM,
    REASON_UNAVAILABLE,
    WORKFLOW_STEPS,
    WorkflowsDeps,
)

H = {"Authorization": "Bearer t"}

ACCOUNT_ID = "186052668426"
MACHINE_ARN = f"arn:aws:states:us-east-1:{ACCOUNT_ID}:stateMachine:uplift-provisioning"
EXEC_ARN_PREFIX = f"arn:aws:states:us-east-1:{ACCOUNT_ID}:execution:uplift-provisioning"

STEP_IDS = ["signup", "verify", "pay", "provision", "activate"]


class FakeVerifier:
    def verify(self, token):
        return {"sub": "uA", "custom:tenant_id": "A", "email": "a@x.com"}


class FakeSfnClient:
    """In-memory stepfunctions client. Records every call so tests can assert the route is
    read-only (list_executions ONLY — never Describe*/Start/Stop) and steered by the
    configured ARN. `error` raises from list_executions to drive the degrade paths."""

    def __init__(self, executions=None, error: Exception | None = None):
        self.executions = list(executions or [])
        self.error = error
        self.calls: list[tuple] = []

    def list_executions(self, **kw):
        self.calls.append(("list_executions", kw))
        if self.error is not None:
            raise self.error
        return {"executions": [dict(e) for e in self.executions]}

    def __getattr__(self, name):
        # Any OTHER api call (describe_state_machine, describe_execution, start_execution…)
        # is a contract violation — record loudly and fail the route.
        def _refuse(**kw):
            self.calls.append((name, kw))
            raise AssertionError(f"workflows route must never call {name}")
        return _refuse


class _AccessDeniedException(Exception):
    """Botocore-shaped access denial (the modeled SFN exception name + response dict)."""

    def __init__(self):
        super().__init__("User is not authorized to perform: states:ListExecutions")
        self.response = {
            "Error": {"Code": "AccessDeniedException",
                      "Message": f"not authorized on {MACHINE_ARN}"},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        }


class _ThrottlingException(Exception):
    def __init__(self):
        super().__init__("Rate exceeded")
        self.response = {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"},
                         "ResponseMetadata": {"HTTPStatusCode": 400}}


def _executions():
    return [
        {
            "executionArn": f"{EXEC_ARN_PREFIX}:provision-acct-1",
            "stateMachineArn": MACHINE_ARN,
            "name": "provision-acct-1",
            "status": "SUCCEEDED",
            "startDate": dt.datetime(2026, 6, 9, 12, 0, 0, tzinfo=dt.UTC),
            "stopDate": dt.datetime(2026, 6, 9, 12, 0, 42, tzinfo=dt.UTC),
        },
        {
            "executionArn": f"{EXEC_ARN_PREFIX}:provision-acct-2",
            "stateMachineArn": MACHINE_ARN,
            "name": "provision-acct-2",
            "status": "RUNNING",
            "startDate": dt.datetime(2026, 6, 10, 9, 30, 0, tzinfo=dt.UTC),
            # no stopDate while running — the route must serialize None, not crash
        },
        {
            "executionArn": f"{EXEC_ARN_PREFIX}:provision-acct-3",
            "stateMachineArn": MACHINE_ARN,
            "name": "provision-acct-3",
            "status": "FAILED",
            "startDate": dt.datetime(2026, 6, 8, 8, 0, 0, tzinfo=dt.UTC),
            "stopDate": dt.datetime(2026, 6, 8, 8, 1, 7, tzinfo=dt.UTC),
        },
    ]


def _client(workflows=None):
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
        workflows=workflows if workflows is not None else WorkflowsDeps(),
    )
    return TestClient(create_app(deps))


def _assert_static_diagram(body: dict) -> None:
    """The OWNED diagram must ride EVERY response shape: 5 steps, in funnel order, each
    with a non-empty label + description."""
    assert body["step_count"] == 5
    assert [s["id"] for s in body["steps"]] == STEP_IDS
    for step in body["steps"]:
        assert step["label"].strip()
        assert len(step["description"]) > 20
    assert body["machine"]["name"] == "uplift-provisioning"
    assert body["machine"]["kind"] == "provisioning"


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_unauth_401():
    client = _client(WorkflowsDeps(state_machine_arn=MACHINE_ARN,
                                   sfn_client=FakeSfnClient(_executions())))
    assert client.get("/workflows").status_code == 401


# --------------------------------------------------------------------------- #
# success shape — fake sfn client
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_success_shape_executions_with_name_status_timestamps_only():
    sfn = FakeSfnClient(_executions())
    client = _client(WorkflowsDeps(state_machine_arn=MACHINE_ARN, sfn_client=sfn))
    r = client.get("/workflows", headers=H)
    assert r.status_code == 200
    body = r.json()
    _assert_static_diagram(body)
    assert body["executions_available"] is True
    assert body["reason"] is None
    execs = body["recent_executions"]
    assert [e["name"] for e in execs] == ["provision-acct-1", "provision-acct-2",
                                          "provision-acct-3"]
    assert [e["status"] for e in execs] == ["SUCCEEDED", "RUNNING", "FAILED"]
    # Datetimes leave as ISO strings; a still-running execution has stopped_at null.
    assert execs[0]["started_at"] == "2026-06-09T12:00:00+00:00"
    assert execs[0]["stopped_at"] == "2026-06-09T12:00:42+00:00"
    assert execs[1]["stopped_at"] is None
    # name + status + timestamps ONLY — no arn keys, no input/output ever.
    assert all(set(e) == {"name", "status", "started_at", "stopped_at"} for e in execs)
    # The read was steered by the CONFIGURED ARN, capped at the module max, and the fake
    # saw list_executions ONLY (read-only contract — no Describe*, no Start/Stop).
    assert sfn.calls == [("list_executions",
                          {"stateMachineArn": MACHINE_ARN, "maxResults": MAX_EXECUTIONS})]


@pytest.mark.integration
def test_arns_and_account_id_never_in_response_body():
    # THE STRIPPING RULE: list_executions returns executionArn + stateMachineArn (both
    # carry the AWS account id); none of it — nor any 'arn:' fragment — may leave the API.
    client = _client(WorkflowsDeps(state_machine_arn=MACHINE_ARN,
                                   sfn_client=FakeSfnClient(_executions())))
    r = client.get("/workflows", headers=H)
    assert r.status_code == 200
    assert "arn:" not in r.text
    assert ACCOUNT_ID not in r.text
    assert MACHINE_ARN not in r.text


@pytest.mark.integration
def test_smuggled_tenant_params_ignored():
    # THE TRUST RULE: the route is gated by the verified claims; query smuggling neither
    # errors nor changes the read (there is no tenant-variant data here to leak).
    sfn = FakeSfnClient(_executions())
    client = _client(WorkflowsDeps(state_machine_arn=MACHINE_ARN, sfn_client=sfn))
    r = client.get("/workflows?tenant_id=B&tenant=B", headers=H)
    assert r.status_code == 200
    assert sfn.calls == [("list_executions",
                          {"stateMachineArn": MACHINE_ARN, "maxResults": MAX_EXECUTIONS})]


# --------------------------------------------------------------------------- #
# the honest degrades — AccessDenied (REQ-009 pending), other AWS failure
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_access_denied_degrades_to_200_with_pending_iam_reason():
    # THE KNOWN CONSTRAINT (verified live): the api task role has states:StartExecution
    # ONLY. The route must answer 200 — diagram intact, executions honestly unavailable,
    # the reason naming the queued grant — never a 500 and never a raw AWS error.
    client = _client(WorkflowsDeps(state_machine_arn=MACHINE_ARN,
                                   sfn_client=FakeSfnClient(error=_AccessDeniedException())))
    r = client.get("/workflows", headers=H)
    assert r.status_code == 200
    body = r.json()
    _assert_static_diagram(body)
    assert body["executions_available"] is False
    assert body["reason"] == REASON_PENDING_IAM
    assert body["recent_executions"] == []
    # The AWS error message (which names the machine ARN) must not leak.
    assert "arn:" not in r.text
    assert ACCOUNT_ID not in r.text
    assert "not authorized" not in r.text


@pytest.mark.integration
def test_other_aws_failure_degrades_to_200_generic_reason_no_leak():
    client = _client(WorkflowsDeps(state_machine_arn=MACHINE_ARN,
                                   sfn_client=FakeSfnClient(error=_ThrottlingException())))
    r = client.get("/workflows", headers=H)
    assert r.status_code == 200
    body = r.json()
    _assert_static_diagram(body)
    assert body["executions_available"] is False
    assert body["reason"] == REASON_UNAVAILABLE
    assert body["recent_executions"] == []
    assert "Rate exceeded" not in r.text
    assert "Throttling" not in r.text


# --------------------------------------------------------------------------- #
# not configured (no ARN) — the live posture today
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_no_arn_answers_not_configured_and_never_builds_a_client():
    class _Bomb:
        def __getattr__(self, name):
            raise AssertionError("no AWS call may happen when the ARN is unset")

    client = _client(WorkflowsDeps(state_machine_arn=None, sfn_client=_Bomb()))
    r = client.get("/workflows", headers=H)
    assert r.status_code == 200
    body = r.json()
    _assert_static_diagram(body)
    assert body["executions_available"] is False
    assert body["reason"] == REASON_NOT_CONFIGURED
    assert body["recent_executions"] == []


@pytest.mark.integration
def test_default_apideps_mounts_route_with_honest_inert_stub():
    # ApiDeps without an explicit `workflows` builds the INERT default stub — the route
    # must mount and answer the honest not-configured 200 (not a 404, not an invented
    # execution list), and constructing the deps must never build a boto3 client.
    deps = ApiDeps(
        verifier=FakeVerifier(), greenlight=Greenlight(), saved_views=SavedViews(),
        conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
        executor=lambda a: None,
    )
    client = TestClient(create_app(deps))
    r = client.get("/workflows", headers=H)
    assert r.status_code == 200
    assert r.json()["reason"] == REASON_NOT_CONFIGURED


# --------------------------------------------------------------------------- #
# READ-ONLY guarantee
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_route_is_read_only_405_on_writes():
    sfn = FakeSfnClient(_executions())
    client = _client(WorkflowsDeps(state_machine_arn=MACHINE_ARN, sfn_client=sfn))
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/workflows", headers=H).status_code == 405
    client.get("/workflows", headers=H)
    # Every recorded call is list_executions — no Start/Stop/Describe path exists at all.
    assert {c[0] for c in sfn.calls} == {"list_executions"}


# --------------------------------------------------------------------------- #
# the static diagram itself — owned semantics pinned
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_step_diagram_tells_the_owned_story():
    # The 5 steps are the OWNED funnel in order, and the descriptions carry the load-bearing
    # honesty claims: webhook-only pay, idempotent/parked provisioning, draft gate,
    # Greenlight sign-off.
    assert [s["id"] for s in WORKFLOW_STEPS] == STEP_IDS
    text = " ".join(s["description"] for s in WORKFLOW_STEPS)
    assert "signed Stripe webhook" in text
    assert "never the browser redirect" in text
    assert "idempotent" in text
    assert "parks the account" in text
    assert "draft-gated" in text
    assert "Greenlight" in text


# --------------------------------------------------------------------------- #
# IMPORT SAFETY — the image-fileset discipline, extended to boto3
# --------------------------------------------------------------------------- #
REPO = Path(__file__).resolve().parents[2]

_PROBE = r"""
import sys

class _BlockBoto3:
    # Simulates a fileset/runtime without boto3: importing the workflows route — and
    # building the whole app with default deps — must not need it (lazy, request-path only).
    def find_module(self, fullname, path=None):  # legacy hook (harmless)
        return None
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "boto3" or fullname.startswith("boto3."):
            raise ModuleNotFoundError(f"No module named {fullname!r} (import-safety probe)")
        return None

sys.meta_path.insert(0, _BlockBoto3())
for mod in [m for m in list(sys.modules) if m == "boto3" or m.startswith("boto3.")]:
    del sys.modules[mod]

import api.workflows_routes  # noqa: E402 — the module under test
import api.app  # noqa: E402 — mounts the route via the inert default deps

from api.app import ApiDeps, create_app  # noqa: E402
from api.views import SavedViews  # noqa: E402
from api.control.autonomy import AutonomyConfig  # noqa: E402
from api.control.greenlight import Greenlight  # noqa: E402

class _V:
    def verify(self, token):
        return {"sub": "u", "custom:tenant_id": "A", "email": "a@x.com"}

app = create_app(ApiDeps(
    verifier=_V(), greenlight=Greenlight(), saved_views=SavedViews(),
    conversation_factory=lambda t: None, autonomy_config=AutonomyConfig(),
    executor=lambda a: None,
))

from fastapi.testclient import TestClient  # noqa: E402
r = TestClient(app).get("/workflows", headers={"Authorization": "Bearer t"})
assert r.status_code == 200, r.status_code
assert r.json()["reason"] == "not configured", r.json()
assert "boto3" not in sys.modules, "boto3 leaked into the import graph"
print("WORKFLOWS-IMPORT-SAFE-OK")
"""


@pytest.mark.integration
def test_workflows_route_imports_and_serves_without_boto3():
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO)},
    )
    assert proc.returncode == 0, (
        f"workflows route needed boto3 at import/mount time:\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "WORKFLOWS-IMPORT-SAFE-OK" in proc.stdout
