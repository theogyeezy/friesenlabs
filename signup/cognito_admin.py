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

    def set_signup_password(self, sub: str, password: str) -> None:
        """Set the user's chosen password immediately at signup (never log or store it).

        Called from the signup route when the user supplies a password during account creation.
        Uses ``admin_set_user_password(Permanent=True)`` to flip the admin-created user from
        ``FORCE_CHANGE_PASSWORD`` straight to ``CONFIRMED`` with the user's real credential —
        so first login works with what they typed, without any "Forgot password" detour.

        Security contract:
          * The password is received over HTTPS, held only in this call frame, and passed
            directly to Cognito — it is NEVER logged, stored in the DB, or echoed in a response.
          * ``email_verified`` is NOT set here (email is not yet verified at signup time; the
            provisioning ``confirm()`` step sets it after verify-before-pay is satisfied).
          * Idempotent: if the user is already CONFIRMED (e.g. from a duplicate signup call),
            this is a no-op so a replayed request never clobbers a password the user has set.
        """
        client = self._cidp()
        # Guard: never clobber a password after the user is already CONFIRMED (idempotency).
        if hasattr(client, "admin_get_user"):
            user = client.admin_get_user(UserPoolId=self._pool_id, Username=sub)
            if user.get("UserStatus", "") == "CONFIRMED":
                return
        client.admin_set_user_password(
            UserPoolId=self._pool_id,
            Username=sub,
            Password=password,   # caller's credential — never logged or stored
            Permanent=True,
        )

    def confirm(self, sub: str) -> None:
        """Flip the user usable after provisioning (step 4, after set_tenant_id).

        FIXED (revenue lane — "newly provisioned users cannot log in"): a user minted by
        ``admin_create_user`` lands in FORCE_CHANGE_PASSWORD, and the Hosted UI rejects a
        password-flow login from there (and ``admin_confirm_sign_up`` does NOT apply — it only
        transitions SELF-signed-up UNCONFIRMED users). The act that actually CONFIRMs an
        admin-created user is ``admin_set_user_password(..., Permanent=True)``:

          * we check the live ``UserStatus`` first;
          * CONFIRMED (either via an earlier ``set_signup_password`` call or a re-run) — the
            user already has a usable credential. We still set ``email_verified`` true here
            (verify-before-pay is satisfied by this point), so the Hosted UI forgot-password
            flow can deliver a reset code if ever needed. The password itself is never touched
            (a re-run can never clobber a password the user has since changed);
          * FORCE_CHANGE_PASSWORD — no user-supplied password was stored (older client or
            back-compat): set a GENERATED, single-use, immediately-discarded strong password
            (Permanent=True flips the user to CONFIRMED) and mark ``email_verified`` true.
            The user onboards via the Hosted UI "Forgot your password?" flow. No credential
            ever travels by email or persists anywhere;
          * the legacy UNCONFIRMED path (self-signup pools) keeps the old
            ``admin_confirm_sign_up`` + narrow already-CONFIRMED tolerance.
        """
        client = self._cidp()
        status = ""
        if hasattr(client, "admin_get_user"):
            user = client.admin_get_user(UserPoolId=self._pool_id, Username=sub)
            status = user.get("UserStatus", "")
        if status == "CONFIRMED":
            # The user already has a real credential (either from set_signup_password or a prior
            # confirm run). Never reset the password. But DO set email_verified=true: by the
            # time confirm() is called (provisioning step 4), verify-before-pay is satisfied,
            # and the Cognito flag must be true for the Hosted UI forgot-password flow.
            client.admin_update_user_attributes(
                UserPoolId=self._pool_id,
                Username=sub,
                UserAttributes=[{"Name": "email_verified", "Value": "true"}],
            )
            return
        if status == "FORCE_CHANGE_PASSWORD":
            # Back-compat / no user-supplied password: set a generated throwaway credential to
            # flip the user to CONFIRMED (the documented working path for admin-created users).
            # The generated password is discarded — never stored or sent.
            client.admin_set_user_password(
                UserPoolId=self._pool_id,
                Username=sub,
                Password=generate_permanent_password(),  # discarded — never stored or sent
                Permanent=True,
            )
            # Our Resend flow verified the address (verify-before-pay) — flip the Cognito flag
            # so the Hosted UI forgot-password flow can deliver the user's real first password.
            client.admin_update_user_attributes(
                UserPoolId=self._pool_id,
                Username=sub,
                UserAttributes=[{"Name": "email_verified", "Value": "true"}],
            )
            return
        try:
            client.admin_confirm_sign_up(UserPoolId=self._pool_id, Username=sub)
        except client.exceptions.NotAuthorizedException as e:
            # Idempotency is ONLY for the already-CONFIRMED replay. Cognito raises
            # NotAuthorizedException both for that AND for real authorization failures (missing
            # IAM perms, disabled user, wrong pool) — swallowing those would mark a broken
            # provisioning step as done. Match the status wording narrowly; re-raise the rest.
            # The already-confirmed message is "User cannot be confirmed. Current status is
            # CONFIRMED" — note the substring must not also match "... status is UNCONFIRMED"
            # (it doesn't: "is CONFIRMED" != "is UNCONFIRMED").
            if "status is CONFIRMED" not in str(e):
                raise


def generate_permanent_password(length: int = 32) -> str:
    """A strong random password satisfying every Cognito policy class (upper/lower/digit/symbol).

    Single-use credential material for ``admin_set_user_password(Permanent=True)`` — generated,
    used for the one API call, and discarded. Never logged, stored, or transmitted.
    """
    import secrets as _secrets  # noqa: PLC0415 — stdlib, imported where used
    import string  # noqa: PLC0415

    classes = [string.ascii_uppercase, string.ascii_lowercase, string.digits, "!@#$%^&*()-_=+"]
    alphabet = "".join(classes)
    chars = [_secrets.choice(c) for c in classes]
    chars += [_secrets.choice(alphabet) for _ in range(max(length, 12) - len(chars))]
    # Order-shuffle with CSPRNG draws (avoid random.shuffle's non-crypto PRNG).
    for i in range(len(chars) - 1, 0, -1):
        j = _secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


def from_config(cfg: Any = None) -> CognitoAdminClient:
    """Build a CognitoAdminClient from shared.config (empty env => clean unconfigured stub)."""
    from shared import config as shared_config  # noqa: PLC0415 — keep module import light
    cfg = cfg or shared_config.load()
    return CognitoAdminClient(cfg.cognito_user_pool_id, region=cfg.aws_region)
