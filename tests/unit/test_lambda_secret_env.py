"""Unit: the provisioning Lambda's cold-start `_resolve_secret_env` (PR #181's contract).

After infra/modules/provisioning_lambda moved to ARN-reference env (`*_SECRET_ARN`), the
resolved VALUE env names (`DB_USER`/`DB_PASS`, `RESEND_API_KEY`, `ANTHROPIC_ADMIN_KEY`,
`POSTHOG_PROJECT_KEY_VALUE`, `ANTHROPIC_API_KEY`, `UPLIFT_ENV_ID`) no longer exist on the
function — the handler must resolve them itself at cold start, mirroring the boto3
`GetSecretValue` fetch `api/migrate.py:_secret` already does for `CRM_APP_SECRET_ARN`.

Proves:
  * the exact ARN-env -> value-env mapping (CRM_APP json {username,password} -> DB_USER/DB_PASS;
    every other secret is a plain string copied verbatim);
  * each ARN var is OPTIONAL — absent vars are skipped, and with NO ARN present no Secrets
    Manager client is ever touched (the all-stub posture needs no AWS at all);
  * resolution failures RAISE loudly (missing secret / malformed crm json) — never a silent
    fall-through to stubs (the SFN execution history is the alarm surface);
  * cold start ordering — `_get_provisioner` resolves secrets BEFORE `build_provisioner`, and
    a resolution failure leaves the singleton unset so the next invocation retries;
  * import safety — the module-level import touches no boto3 (the client is built lazily and
    only when at least one ARN is present).
"""
from __future__ import annotations

import pytest

import signup.lambda_handler as lambda_handler
from signup.lambda_handler import _SECRET_ENV_MAP, _resolve_secret_env

ALL_ARN_ENVS = [arn_env for arn_env, _ in _SECRET_ENV_MAP]
ALL_VALUE_ENVS = [name for _, targets in _SECRET_ENV_MAP for name in targets]


class FakeSecretsManager:
    """boto3 secretsmanager stand-in: arn -> SecretString, recording every call."""

    def __init__(self, secrets: dict[str, str]):
        self.secrets = dict(secrets)
        self.calls: list[str] = []

    def get_secret_value(self, SecretId):  # noqa: N803 — boto3 casing
        self.calls.append(SecretId)
        if SecretId not in self.secrets:
            raise RuntimeError(f"ResourceNotFoundException: {SecretId}")
        return {"SecretString": self.secrets[SecretId]}


@pytest.fixture(autouse=True)
def _clean_env():
    """No ARN/value env bleeds between tests (or in from the developer's shell) — and none of
    the values `_resolve_secret_env` writes DIRECTLY to os.environ leaks OUT of a test (a
    leaked UPLIFT_ENV_ID/DB_USER would flip unrelated env-gated tests). Full save/restore —
    monkeypatch can't undo writes it never made."""
    import os
    names = ALL_ARN_ENVS + ALL_VALUE_ENVS + ["DB_HOST", "DB_NAME", "DB_PORT", "UPLIFT_DB_URL"]
    saved = {n: os.environ.get(n) for n in names}
    for n in names:
        os.environ.pop(n, None)
    yield
    for n, v in saved.items():
        if v is None:
            os.environ.pop(n, None)
        else:
            os.environ[n] = v


# ---------------- the mapping ----------------
@pytest.mark.unit
def test_resolves_every_arn_env_into_the_value_names_config_reads(monkeypatch):
    arns = {arn_env: f"arn:aws:secretsmanager:us-east-1:1:secret:{arn_env}" for arn_env in ALL_ARN_ENVS}
    for arn_env, arn in arns.items():
        monkeypatch.setenv(arn_env, arn)
    sm = FakeSecretsManager({
        arns["CRM_APP_SECRET_ARN"]: '{"username": "crm_app", "password": "pw-1"}',
        arns["RESEND_API_KEY_SECRET_ARN"]: "re_key",
        arns["ANTHROPIC_ADMIN_KEY_SECRET_ARN"]: "sk-ant-admin-1",
        arns["POSTHOG_PROJECT_KEY_SECRET_ARN"]: "phc_key",
        arns["ANTHROPIC_API_KEY_SECRET_ARN"]: "sk-ant-api-1",
        arns["UPLIFT_ENV_ID_SECRET_ARN"]: "env_123",
    })

    resolved = _resolve_secret_env(sm=sm)

    import os
    assert os.environ["DB_USER"] == "crm_app"
    assert os.environ["DB_PASS"] == "pw-1"
    assert os.environ["RESEND_API_KEY"] == "re_key"
    assert os.environ["ANTHROPIC_ADMIN_KEY"] == "sk-ant-admin-1"
    assert os.environ["POSTHOG_PROJECT_KEY_VALUE"] == "phc_key"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-api-1"
    assert os.environ["UPLIFT_ENV_ID"] == "env_123"
    # Every ARN was fetched exactly once, by its full ARN (GetSecretValue accepts the ARN).
    assert sorted(sm.calls) == sorted(arns.values())
    # The return value names what was SET (names only — never values).
    assert sorted(resolved) == sorted(ALL_VALUE_ENVS)


@pytest.mark.unit
def test_crm_app_dsn_parts_feed_dsn_from_env(monkeypatch):
    """The resolved DB_USER/DB_PASS + the plain DB_HOST/DB_NAME the module already injects
    must assemble the crm_app DSN through the SAME shared.config.dsn_from_env the API uses."""
    from shared.config import dsn_from_env

    monkeypatch.delenv("UPLIFT_DB_URL", raising=False)
    monkeypatch.setenv("CRM_APP_SECRET_ARN", "arn:crm")
    monkeypatch.setenv("DB_HOST", "db.internal")
    monkeypatch.setenv("DB_NAME", "uplift")
    monkeypatch.delenv("DB_PORT", raising=False)
    sm = FakeSecretsManager({"arn:crm": '{"username": "crm_app", "password": "s3cret"}'})

    _resolve_secret_env(sm=sm)

    assert dsn_from_env() == "postgresql://crm_app:s3cret@db.internal:5432/uplift"


@pytest.mark.unit
def test_arn_resolved_value_overwrites_a_preset_value_env(monkeypatch):
    """The ARN is the source of truth on the function — a stale pre-set value env (e.g. left
    over from the pre-#181 plan-time wiring) must not shadow the freshly rotated secret."""
    monkeypatch.setenv("RESEND_API_KEY", "stale")
    monkeypatch.setenv("RESEND_API_KEY_SECRET_ARN", "arn:resend")
    sm = FakeSecretsManager({"arn:resend": "fresh"})

    _resolve_secret_env(sm=sm)

    import os
    assert os.environ["RESEND_API_KEY"] == "fresh"


# ---------------- optionality / stub posture ----------------
@pytest.mark.unit
def test_absent_arn_vars_are_skipped(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY_SECRET_ARN", "arn:resend")
    sm = FakeSecretsManager({"arn:resend": "re_key"})

    resolved = _resolve_secret_env(sm=sm)

    import os
    assert resolved == ["RESEND_API_KEY"]
    assert sm.calls == ["arn:resend"]
    for name in set(ALL_VALUE_ENVS) - {"RESEND_API_KEY"}:
        assert name not in os.environ, f"{name} must not be set when its ARN env is absent"


@pytest.mark.unit
def test_no_arn_env_means_no_boto3_and_no_client(monkeypatch):
    """All-stub posture: with zero ARN vars present, _resolve_secret_env must return before
    building any Secrets Manager client — even importing boto3 would be wrong here."""
    import builtins

    real_import = builtins.__import__

    def deny_boto3(name, *args, **kwargs):
        if name == "boto3" or name.startswith("boto3."):
            raise AssertionError("boto3 must not be imported when no *_SECRET_ARN is present")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny_boto3)
    assert _resolve_secret_env() == []  # sm=None and STILL no boto3 — the early return wins


@pytest.mark.unit
def test_empty_string_arn_is_treated_as_absent(monkeypatch):
    """Terraform's safe '' default (entry omitted-or-empty) must read as unset, not as an ARN."""
    monkeypatch.setenv("UPLIFT_ENV_ID_SECRET_ARN", "")
    sm = FakeSecretsManager({})
    assert _resolve_secret_env(sm=sm) == []
    assert sm.calls == []


# ---------------- failures raise loudly ----------------
@pytest.mark.unit
def test_resolution_failure_raises_never_stubs(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_ADMIN_KEY_SECRET_ARN", "arn:missing")
    sm = FakeSecretsManager({})  # the secret does not exist / access denied

    with pytest.raises(RuntimeError, match="ResourceNotFound"):
        _resolve_secret_env(sm=sm)

    import os
    assert "ANTHROPIC_ADMIN_KEY" not in os.environ


@pytest.mark.unit
def test_malformed_crm_json_raises(monkeypatch):
    monkeypatch.setenv("CRM_APP_SECRET_ARN", "arn:crm")
    for junk in ("not-json", '{"username": "only-half"}'):
        sm = FakeSecretsManager({"arn:crm": junk})
        with pytest.raises((ValueError, KeyError)):
            _resolve_secret_env(sm=sm)


# ---------------- cold-start ordering ----------------
@pytest.mark.unit
def test_cold_start_resolves_secrets_before_build_provisioner(monkeypatch):
    order: list[str] = []

    monkeypatch.setattr(lambda_handler, "_PROVISIONER", None)
    monkeypatch.setattr(lambda_handler, "_resolve_secret_env",
                        lambda sm=None: order.append("resolve"))

    import api.prod_deps as prod_deps

    def fake_build():
        order.append("build")
        return "the-provisioner"

    monkeypatch.setattr(prod_deps, "build_provisioner", fake_build)

    assert lambda_handler._get_provisioner() == "the-provisioner"
    assert order == ["resolve", "build"]
    # The singleton is cached: a second call neither re-resolves nor rebuilds.
    assert lambda_handler._get_provisioner() == "the-provisioner"
    assert order == ["resolve", "build"]


@pytest.mark.unit
def test_cold_start_failure_leaves_singleton_unset_for_retry(monkeypatch):
    monkeypatch.setattr(lambda_handler, "_PROVISIONER", None)

    def boom(sm=None):
        raise RuntimeError("AccessDeniedException")

    monkeypatch.setattr(lambda_handler, "_resolve_secret_env", boom)
    with pytest.raises(RuntimeError, match="AccessDenied"):
        lambda_handler._get_provisioner()
    # SFN retries the invocation; the next attempt must retry resolution, not serve a
    # half-configured cached runtime.
    assert lambda_handler._PROVISIONER is None


# ---------------- import safety ----------------
@pytest.mark.unit
def test_module_import_is_boto3_free():
    """Importing signup.lambda_handler must not import boto3 (module docstring guarantee).
    Run in a SUBPROCESS so the parent test run's already-imported modules can't mask it."""
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    probe = (
        "import sys\n"
        "class _Block:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname == 'boto3' or fullname.startswith('boto3.'):\n"
        "            raise ModuleNotFoundError('boto3 blocked (import-safety probe)')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Block())\n"
        "for m in [m for m in list(sys.modules) if m == 'boto3' or m.startswith('boto3.')]:\n"
        "    del sys.modules[m]\n"
        "import signup.lambda_handler\n"
        "print('LAMBDA-IMPORT-OK')\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe], cwd=repo, capture_output=True,
                          text=True, timeout=120,
                          env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(repo)})
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "LAMBDA-IMPORT-OK" in proc.stdout
