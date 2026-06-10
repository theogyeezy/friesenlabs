"""Real Cognito admin ops — identity plane (TODO INT/P0 "Implement real Cognito admin ops").

Drop-in for `api/prod_deps._StubCognito` behind the duck-type contract that
`signup/accounts.py` (``create_unconfirmed_user``) and `signup/provisioning.py`
(``set_tenant_id`` / ``confirm``) already call.

THE TRUST RULE — the write side: downstream, tenant identity is read ONLY from the verified JWT
``custom:tenant_id`` claim. That is only sound because the claim is WRITTEN only here, via
admin-only IAM (``cognito-idp:AdminUpdateUserAttributes`` on the api task role — the app client
must NOT have write access to the attribute). The ``tenant_id`` arrives as a PARAMETER from the
provisioning pipeline that minted it — never from env, header, or request body.

Idempotent: a re-submitted signup tolerates ``UsernameExistsException`` (returns the existing
user's sub); a re-run ``confirm`` tolerates the already-CONFIRMED error; attribute set is a plain
overwrite.

Unconfigured == clean stub: with no injected client and no ``pool_id``, every call raises
:class:`CognitoNotConfiguredError` before boto3 is even imported. Import-safe: boto3 is imported
lazily on first real use.
"""
from __future__ import annotations

from typing import Any


class CognitoNotConfiguredError(RuntimeError):
    """A Cognito admin call was attempted without the required configuration."""


def _sub(attributes: list[dict]) -> str:
    """Pull the immutable ``sub`` out of a Cognito attribute list."""
    for attr in attributes:
        if attr.get("Name") == "sub":
            return attr["Value"]
    raise KeyError("Cognito response carried no 'sub' attribute")


class CognitoAdminClient:
    """Thin adapter over boto3 ``cognito-idp`` admin ops (admin-only IAM; never the app client)."""

    def __init__(self, pool_id: str, *, region: str | None = None, client: Any = None):
        self._pool_id = pool_id or ""
        self._region = region
        self._client = client  # injected fake in tests; lazily built otherwise

    # ---------------------------------------------------------------- internals
    def _cidp(self) -> Any:
        if not self._pool_id:
            raise CognitoNotConfiguredError(
                "COGNITO_USER_POOL_ID not configured — cannot run Cognito admin ops"
            )
        if self._client is None:
            import boto3  # noqa: PLC0415 — lazy: importing this module needs no boto3/network
            from shared import config  # noqa: PLC0415
            self._client = boto3.client(
                "cognito-idp", region_name=self._region or config.load().aws_region
            )
        return self._client

    # ---------------------------------------------------------------- contract
    def create_unconfirmed_user(self, email: str) -> str:
        """Mint the Cognito user at signup — UNCONFIRMED, no Cognito email, NO tenant_id yet."""
        client = self._cidp()
        try:
            resp = client.admin_create_user(
                UserPoolId=self._pool_id,
                Username=email,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    # Email verification is OURS (the signed Resend link in accounts.py), not
                    # Cognito's built-in flow.
                    {"Name": "email_verified", "Value": "false"},
                ],
                # SUPPRESS: Cognito must never send its invite email — comms run through Resend,
                # and the user must not receive sign-in credentials before provisioning confirms.
                MessageAction="SUPPRESS",
            )
            return _sub(resp["User"].get("Attributes", []))
        except client.exceptions.UsernameExistsException:
            # Idempotent: a re-submitted signup returns the existing user's sub instead of
            # crashing or minting a duplicate.
            existing = client.admin_get_user(UserPoolId=self._pool_id, Username=email)
            return _sub(existing.get("UserAttributes", []))

    def set_tenant_id(self, sub: str, tenant_id: str) -> None:
        """THE TRUST RULE's write side: the ONLY writer of the claim the platform trusts.

        Runs under admin-only IAM during provisioning step 4 — after the tenant exists, before
        confirm. Plain attribute overwrite, so a re-delivered webhook re-setting the same value
        is naturally idempotent.
        """
        client = self._cidp()
        client.admin_update_user_attributes(
            UserPoolId=self._pool_id,
            # Admin APIs accept the immutable sub as the username lookup value (what
            # provisioning.py passes as account.cognito_sub).
            Username=sub,
            UserAttributes=[{"Name": "custom:tenant_id", "Value": str(tenant_id)}],
        )

    def confirm(self, sub: str) -> None:
        """Flip the user usable after provisioning (step 4, after set_tenant_id).

        # VERIFY (call ordering / user state): admin_confirm_sign_up only transitions a
        # SELF-signed-up user out of UNCONFIRMED. A user minted by admin_create_user lands in
        # FORCE_CHANGE_PASSWORD instead, where the confirming act is
        # admin_set_user_password(..., Permanent=True) during the user's password setup. The
        # confirm-after-set_tenant_id ordering and the exact status transition must be validated
        # against the live pool (LANE NICK) before this path is trusted end-to-end.
        """
        client = self._cidp()
        try:
            client.admin_confirm_sign_up(UserPoolId=self._pool_id, Username=sub)
        except client.exceptions.NotAuthorizedException as e:
            # Idempotency is ONLY for the already-CONFIRMED replay. Cognito raises
            # NotAuthorizedException both for that AND for real authorization failures (missing
            # IAM perms, disabled user, wrong pool) — swallowing those would mark a broken
            # provisioning step as done. Match the status wording narrowly; re-raise the rest.
            # VERIFY (live pool): the already-confirmed message is
            # "User cannot be confirmed. Current status is CONFIRMED" — note the substring must
            # not also match "... status is UNCONFIRMED" (it doesn't: "is CONFIRMED" != "is
            # UNCONFIRMED"); confirm exact wording against the live pool before trusting e2e.
            if "status is CONFIRMED" not in str(e):
                raise


def from_config(cfg: Any = None) -> CognitoAdminClient:
    """Build a CognitoAdminClient from shared.config (empty env => clean unconfigured stub)."""
    from shared import config as shared_config  # noqa: PLC0415 — keep module import light
    cfg = cfg or shared_config.load()
    return CognitoAdminClient(cfg.cognito_user_pool_id, region=cfg.aws_region)
