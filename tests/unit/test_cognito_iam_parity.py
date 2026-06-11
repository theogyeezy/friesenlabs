"""Unit: IAM-action parity — every Cognito admin op the app actually CALLS must be GRANTed.

The revenue path mints + provisions Cognito users via ``signup.cognito_admin.CognitoAdminClient``.
Two IAM policies must allow exactly those ops, scoped to the one pool:
  * the API task role            — ``infra/modules/iam/main.tf``                (api_task_cognito_signup)
  * the provisioning Lambda role — ``infra/modules/provisioning_lambda/main.tf`` (cognito_signup)

The gap this guards (caught by the adversarial revenue-path review, 2026-06-10): the client calls
``admin_set_user_password`` (the ONLY act that flips an admin-created user out of
FORCE_CHANGE_PASSWORD into CONFIRMED — i.e. lets a freshly provisioned, paid tenant log in), but
neither policy listed ``cognito-idp:AdminSetUserPassword`` — so paid provisioning parked at the
Cognito-confirm step with an AccessDenied.

This test DERIVES the required action set from the client source (every ``client.admin_*(`` call),
never a frozen list — so a new admin op added to the client without a matching GRANT fails here
instead of in prod. It then statically parses both .tf policies and asserts the derived set is a
subset of each. No AWS, no terraform binary — a pure static parity gate.
"""
from __future__ import annotations

import os
import re

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CLIENT_SRC = os.path.join(ROOT, "signup", "cognito_admin.py")
API_IAM_TF = os.path.join(ROOT, "infra", "modules", "iam", "main.tf")
LAMBDA_IAM_TF = os.path.join(ROOT, "infra", "modules", "provisioning_lambda", "main.tf")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _snake_to_pascal(method: str) -> str:
    """boto3 method name -> IAM action suffix: admin_set_user_password -> AdminSetUserPassword."""
    return "".join(part.capitalize() for part in method.split("_"))


def required_cognito_actions(client_src: str) -> set[str]:
    """Every ``cognito-idp:<Action>`` the client invokes, derived from its ``client.admin_*(``
    calls. Comments/docstrings are stripped so a method only named in prose doesn't count."""
    # Drop full-line comments and docstring bodies-ish: we only want real call sites, so match
    # on the call syntax ``client.admin_<name>(`` which never appears in the narrative prose.
    methods = set(re.findall(r"client\.(admin_[a-z_]+)\(", client_src))
    return {f"cognito-idp:{_snake_to_pascal(m)}" for m in methods}


def granted_cognito_actions(tf_src: str) -> set[str]:
    """Every ``cognito-idp:*`` action string present in a .tf file (the IAM policy Action list)."""
    return set(re.findall(r'"(cognito-idp:[A-Za-z]+)"', tf_src))


_REQUIRED = required_cognito_actions(_read(CLIENT_SRC))


@pytest.mark.unit
def test_required_action_set_is_derived_and_nonempty():
    """Guard the derivation machinery: the client must expose the known admin ops, so a regex
    that goes blind fails loudly instead of shrinking the parity gate to an empty (always-pass)
    set."""
    assert _REQUIRED >= {
        "cognito-idp:AdminCreateUser",
        "cognito-idp:AdminGetUser",
        "cognito-idp:AdminUpdateUserAttributes",
        "cognito-idp:AdminSetUserPassword",
        "cognito-idp:AdminConfirmSignUp",
    }, f"client admin-op derivation looks wrong: {_REQUIRED}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "tf_path",
    [API_IAM_TF, LAMBDA_IAM_TF],
    ids=["api_task_role", "provisioning_lambda_role"],
)
def test_iam_policy_grants_every_called_cognito_action(tf_path):
    """Parity: every Cognito admin action the client CALLS is GRANTed in this policy. The
    AdminSetUserPassword gap (paid provisioning parked at Cognito-confirm) fails here."""
    granted = granted_cognito_actions(_read(tf_path))
    missing = _REQUIRED - granted
    assert not missing, (
        f"{os.path.relpath(tf_path, ROOT)} is missing Cognito actions the client calls: "
        f"{sorted(missing)} (granted: {sorted(granted)})"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "tf_path",
    [API_IAM_TF, LAMBDA_IAM_TF],
    ids=["api_task_role", "provisioning_lambda_role"],
)
def test_iam_policy_grants_no_unused_cognito_action(tf_path):
    """Least-privilege: the policy grants no Cognito admin action the client never calls, so the
    two policies and the client stay exactly in lockstep (a stale broad grant fails here)."""
    granted = granted_cognito_actions(_read(tf_path))
    extra = granted - _REQUIRED
    assert not extra, (
        f"{os.path.relpath(tf_path, ROOT)} grants Cognito actions the client never calls: "
        f"{sorted(extra)} — tighten to the called set {sorted(_REQUIRED)}"
    )
