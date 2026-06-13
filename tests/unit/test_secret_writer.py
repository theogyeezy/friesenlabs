"""Unit: the SecretWriter seam (api/integrations_routes.py).

All offline. Proves:
  * Boto3SecretWriter put path: put_secret_value first, create_secret ONLY on not-found,
    and non-not-found errors (access/throttle) propagate — never silently swallowed
  * secret_exists rides describe_secret (existence only, never GetSecretValue)
  * import safety: constructing the writer touches no AWS
  * build_integrations_deps switch semantics: INTEGRATIONS_REAL_SECRETS exactly "true"/"1"
    (fail closed), sync runner only under the ingest plane's own INGEST_REAL_STORES
"""
import pytest

from api.integrations_routes import (
    Boto3SecretWriter,
    IntegrationsDeps,
    SecretWriter,
    build_integrations_deps,
)
from shared.config import ENV_INTEGRATIONS_REAL_SECRETS

pytestmark = pytest.mark.unit


class ResourceNotFoundException(Exception):
    """Named like the AWS error code — what the writer's not-found check matches."""


class AccessDeniedException(Exception):
    pass


class FakeSm:
    """Fake secretsmanager client; `existing` refs accept put_secret_value/describe_secret."""

    def __init__(self, existing=()):
        self.existing = set(existing)
        self.put_calls = []
        self.create_calls = []
        self.get_calls = []

    def put_secret_value(self, SecretId, SecretString):
        self.put_calls.append((SecretId, SecretString))
        if SecretId not in self.existing:
            raise ResourceNotFoundException(SecretId)

    def create_secret(self, Name, SecretString):
        self.create_calls.append((Name, SecretString))
        self.existing.add(Name)

    def describe_secret(self, SecretId):
        if SecretId not in self.existing:
            raise ResourceNotFoundException(SecretId)
        return {"Name": SecretId}

    def get_secret_value(self, SecretId):  # pragma: no cover — must never be called
        self.get_calls.append(SecretId)
        raise AssertionError("status checks must never read the secret value")


def test_put_existing_secret_uses_put_secret_value_only():
    sm = FakeSm(existing={"uplift/T1/hubspot"})
    w = Boto3SecretWriter(client=sm)
    w.put_secret("uplift/T1/hubspot", "tok")
    assert sm.put_calls == [("uplift/T1/hubspot", "tok")]
    assert sm.create_calls == []


def test_put_missing_secret_falls_back_to_create():
    sm = FakeSm()
    w = Boto3SecretWriter(client=sm)
    w.put_secret("uplift/T1/hubspot", "tok")
    assert sm.create_calls == [("uplift/T1/hubspot", "tok")]
    assert w.secret_exists("uplift/T1/hubspot") is True


def test_non_not_found_error_propagates():
    class DenyingSm(FakeSm):
        def put_secret_value(self, SecretId, SecretString):
            raise AccessDeniedException("nope")

    w = Boto3SecretWriter(client=DenyingSm())
    with pytest.raises(AccessDeniedException):
        w.put_secret("uplift/T1/hubspot", "tok")


def test_secret_exists_never_reads_the_value():
    sm = FakeSm(existing={"uplift/T1/hubspot"})
    w = Boto3SecretWriter(client=sm)
    assert w.secret_exists("uplift/T1/hubspot") is True
    assert w.secret_exists("uplift/T2/hubspot") is False
    assert sm.get_calls == []


def test_writer_satisfies_the_protocol_and_constructs_offline():
    # No client injected: construction must not import boto3 / touch AWS.
    w = Boto3SecretWriter()
    assert isinstance(w, SecretWriter)
    assert w._client is None


# --------------------------------------------------------------------------- #
# env-built default deps — strict switch semantics
# --------------------------------------------------------------------------- #
def test_unset_env_builds_all_stub_deps(monkeypatch):
    monkeypatch.delenv(ENV_INTEGRATIONS_REAL_SECRETS, raising=False)
    monkeypatch.delenv("INGEST_REAL_STORES", raising=False)
    deps = build_integrations_deps()
    assert isinstance(deps, IntegrationsDeps)
    assert deps.secret_writer is None
    assert deps.sync_runner is None


@pytest.mark.parametrize("value", ["true", "1"])
def test_exact_switch_values_select_the_real_writer(monkeypatch, value):
    monkeypatch.setenv(ENV_INTEGRATIONS_REAL_SECRETS, value)
    monkeypatch.delenv("INGEST_REAL_STORES", raising=False)
    deps = build_integrations_deps()
    assert isinstance(deps.secret_writer, Boto3SecretWriter)
    assert deps.sync_runner is None  # the ingest switch is separate and off


@pytest.mark.parametrize("value", ["TRUE", "True", " 1", "yes", "on", "0", ""])
def test_lenient_values_fail_closed(monkeypatch, value):
    monkeypatch.setenv(ENV_INTEGRATIONS_REAL_SECRETS, value)
    assert build_integrations_deps().secret_writer is None


def test_sync_runner_rides_the_ingest_master_switch(monkeypatch):
    # Deploy invariance: DB_* alone never wires the runner — only INGEST_REAL_STORES.
    monkeypatch.delenv(ENV_INTEGRATIONS_REAL_SECRETS, raising=False)
    monkeypatch.setenv("UPLIFT_DB_URL", "postgresql://x:y@localhost/uplift")
    monkeypatch.delenv("INGEST_REAL_STORES", raising=False)
    assert build_integrations_deps().sync_runner is None
    monkeypatch.setenv("INGEST_REAL_STORES", "1")
    runner = build_integrations_deps().sync_runner
    assert callable(runner)  # built lazily — nothing real constructed until called


# --------------------------------------------------------------------------- delete_secret (disconnect)

class FakeSmWithDelete(FakeSm):
    """FakeSm + the delete/DeletedDate surface the disconnect path exercises."""

    def __init__(self, existing=(), deleted_pending=()):
        super().__init__(existing)
        # Refs scheduled for deletion: DescribeSecret still answers, WITH DeletedDate.
        self.deleted_pending = set(deleted_pending)
        self.delete_calls = []

    def describe_secret(self, SecretId):
        if SecretId in self.deleted_pending:
            return {"Name": SecretId, "DeletedDate": "2026-06-11T00:00:00Z"}
        return super().describe_secret(SecretId)

    def delete_secret(self, SecretId, ForceDeleteWithoutRecovery=False):
        self.delete_calls.append((SecretId, ForceDeleteWithoutRecovery))
        if SecretId not in self.existing:
            raise ResourceNotFoundException(SecretId)
        self.existing.discard(SecretId)


def test_delete_secret_forces_immediate_deletion():
    """delete_secret must pass ForceDeleteWithoutRecovery=True: a window-scheduled
    deletion would block a reconnect (put on a deletion-scheduled secret fails)."""
    sm = FakeSmWithDelete(existing={"uplift/A/hubspot"})
    w = Boto3SecretWriter(client=sm)
    assert w.delete_secret("uplift/A/hubspot") is True
    assert sm.delete_calls == [("uplift/A/hubspot", True)]
    assert "uplift/A/hubspot" not in sm.existing


def test_delete_secret_absent_returns_false_idempotent():
    sm = FakeSmWithDelete()
    w = Boto3SecretWriter(client=sm)
    assert w.delete_secret("uplift/A/hubspot") is False  # nothing existed — no error


def test_delete_secret_non_notfound_errors_propagate():
    class _Sm(FakeSmWithDelete):
        def delete_secret(self, SecretId, ForceDeleteWithoutRecovery=False):
            raise AccessDeniedException("simulated IAM gap")

    w = Boto3SecretWriter(client=_Sm())
    with pytest.raises(AccessDeniedException):
        w.delete_secret("uplift/A/hubspot")


def test_secret_exists_false_when_deletion_scheduled():
    """A secret mid-deletion (DeletedDate set) is NOT connected — without this the
    status would read 'connected' for up to 30 days after a (non-forced) delete."""
    sm = FakeSmWithDelete(deleted_pending={"uplift/A/hubspot"})
    w = Boto3SecretWriter(client=sm)
    assert w.secret_exists("uplift/A/hubspot") is False


def test_probe_token_sends_nondefault_user_agent(monkeypatch):
    """GHL/Cloudflare BANS urllib's default UA (error 1010 -> 403), which the prober would misread
    as 'token rejected'. probe_token must send a named UA. Regression guard."""
    import urllib.request

    from api.integrations_routes import probe_token

    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert probe_token("gohighlevel", "tok") is True
    assert seen["ua"] and not seen["ua"].lower().startswith("python-")  # not the banned default
