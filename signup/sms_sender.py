"""SMS OTP delivery via AWS SNS (TODO INT/P1 'Implement real SMS OTP delivery').

Implements the SMS hook the signup stack injects into `AccountService` (signup/accounts.py):
``sms.send_otp(phone, code)`` — phone is the normalized E.164 number, code is the short-lived
one-time code minted by the token layer (this module only delivers; it never generates,
stores, or validates codes).

DRAFT-GATE (CLAUDE.md hard constraint #2): real delivery is refused unless an explicit
``allow_real_sends=True`` flag is passed at construction (default False, wired from
``shared.config.Config.allow_real_sends``). While gated, send_otp logs + returns False and
NEVER constructs a boto3 client — boto3 is imported lazily only on a real send attempt, so
this module is import-safe with no AWS credentials and no network.

Delivery failures raise ``SmsSendError`` (unlike email, a silently lost OTP dead-ends the
verify flow — the caller must know to surface "try again").
"""
from __future__ import annotations

import logging

from . import mask_phone  # PII-safe logging (signup/__init__.py): never log a raw phone number

log = logging.getLogger(__name__)


class SmsSendError(RuntimeError):
    """Raised when a real OTP delivery was attempted and failed (config or transport)."""


def _default_client_factory(region: str):
    """Lazy boto3 — only imported when a real (un-gated) send is attempted."""
    import boto3  # noqa: PLC0415 — deliberate lazy import (offline/import safety)

    return boto3.client("sns", region_name=region)


class SnsSmsOtpSender:
    def __init__(
        self,
        region: str = "us-east-1",
        *,
        allow_real_sends: bool = False,
        client=None,
        client_factory=None,
        product_name: str = "Uplift",
    ):
        self.region = region
        self.allow_real_sends = bool(allow_real_sends)
        self.product_name = product_name
        self._client = client  # injected in tests; None = build lazily via the factory
        self._client_factory = client_factory or _default_client_factory

    def send_otp(self, phone: str, code: str) -> bool:
        """Deliver the verification code to `phone`. Returns True iff handed to SNS.

        Gated (the default) -> logged + False, no client, no network.
        Un-gated transport/config failure -> SmsSendError.
        """
        if not self.allow_real_sends:
            # DRAFT-GATE: refuse real delivery unless explicitly enabled. The phone is MASKED
            # (signup.mask_phone) — logs must never accumulate every signup's raw number.
            log.info("DRAFT-GATE: real sends disabled; dropping OTP SMS to %s", mask_phone(phone))
            return False
        message = (
            f"Your {self.product_name} verification code is {code}. It expires in 10 minutes."
        )
        client = self._get_client()
        try:
            # VERIFY: boto3 SNS direct-publish shape — PhoneNumber + Message, with the
            # AWS.SNS.SMS.SMSType=Transactional attribute so OTPs bypass promotional
            # throttling. Confirm sandbox/spend-limit state before live use (BLOCKED:
            # Lane Nick — SNS SMS spend limit / origination identity).
            client.publish(
                PhoneNumber=phone,
                Message=message,
                MessageAttributes={
                    "AWS.SNS.SMS.SMSType": {
                        "DataType": "String",
                        "StringValue": "Transactional",
                    },
                },
            )
            return True
        except Exception as e:  # noqa: BLE001 — normalize transport errors
            # The error message gets logged by callers — carry the MASKED phone only.
            raise SmsSendError(
                f"SNS publish to {mask_phone(phone)} failed: {type(e).__name__}: {e}"
            ) from e

    def _get_client(self):
        if self._client is None:
            try:
                self._client = self._client_factory(self.region)
            except Exception as e:  # noqa: BLE001 — missing boto3 / creds / region
                raise SmsSendError(
                    f"SNS client unavailable (boto3/credentials not configured): "
                    f"{type(e).__name__}: {e}"
                ) from e
        return self._client
