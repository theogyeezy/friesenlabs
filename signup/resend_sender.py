"""Resend outbound email sender (TODO INT/P0 'Build a real Resend email client').

Implements the two email hooks the signup stack calls:
  - `AccountService` (signup/accounts.py): ``email.send_verification(email, token)`` — the
    second positional is the signed single-use verification token (or a full signed URL).
  - `Provisioner` (signup/provisioning.py): ``resend.send_welcome(account.email, tenant_id)``.

DRAFT-GATE (CLAUDE.md hard constraint #2 — "Draft-only. No tool that sends a real email/SMS
... may run against real data"): every send is refused unless an explicit
``allow_real_sends=True`` flag is passed at construction (default False, wired from
``shared.config.Config.allow_real_sends`` / the ALLOW_REAL_SENDS env var). Gated or
unconfigured sends log and return False — they never raise and never touch the network.

Transport is stdlib ``urllib.request`` (no new dependency); the opener is injectable so
offline tests mock the HTTP seam completely.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from html import escape

log = logging.getLogger(__name__)

# VERIFY: Resend send endpoint — POST https://api.resend.com/emails with
# 'Authorization: Bearer <api_key>' and JSON body {from, to: [...], subject, html};
# 200 response carries {"id": "..."}. Confirm against the live Resend API docs before
# enabling real sends (BLOCKED: Lane Nick — needs Resend key + verified sending domain).
RESEND_API_URL = "https://api.resend.com/emails"

_TIMEOUT_S = 10


def _default_opener(request: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 — https only


def _email_of(account) -> str:
    """Accept an Account-like object (has .email) or a bare email string."""
    return getattr(account, "email", account)


class ResendEmailSender:
    """Minimal templated transactional email over the Resend HTTP API.

    Failure contract: logs-not-raises. The signup flow must never 500 because the mail
    provider hiccuped — verification can be retried, the welcome email is best-effort.
    """

    def __init__(
        self,
        api_key: str,
        from_email: str,
        *,
        allow_real_sends: bool = False,
        verify_url_base: str = "",
        api_url: str = RESEND_API_URL,
        opener=None,
        product_name: str = "Uplift",
    ):
        self.api_key = api_key or ""
        self.from_email = from_email or ""
        self.allow_real_sends = bool(allow_real_sends)
        self.verify_url_base = verify_url_base or ""
        self.api_url = api_url
        self.product_name = product_name
        self._opener = opener or _default_opener

    # ---------------- public hooks (contracts above) ----------------

    def send_verification(self, account, signed_link) -> bool:
        """Send the signed single-use email-verification link. Returns True iff delivered."""
        email = _email_of(account)
        link = self._compose_link(signed_link)
        subject = f"Verify your email for {self.product_name}"
        html = (
            f"<p>Welcome to {escape(self.product_name)}!</p>"
            f"<p>Confirm this email address to continue setting up your account:</p>"
            f'<p><a href="{escape(link, quote=True)}">Verify my email</a></p>'
            f"<p>This link is single-use and expires in 15 minutes. "
            f"If you didn't sign up, you can ignore this email.</p>"
        )
        return self._send(email, subject, html)

    def send_welcome(self, account, tenant_id=None) -> bool:
        """Post-provisioning welcome (Provisioner step 6). Returns True iff delivered."""
        email = _email_of(account)
        subject = f"Your {self.product_name} workspace is ready"
        tenant_line = (
            f"<p>Workspace id: <code>{escape(str(tenant_id))}</code></p>" if tenant_id else ""
        )
        html = (
            f"<p>Your {escape(self.product_name)} instance is provisioned and ready.</p>"
            f"{tenant_line}"
            f"<p>Sign in to start connecting your data.</p>"
        )
        return self._send(email, subject, html)

    # ---------------- internals ----------------

    def _compose_link(self, signed_link) -> str:
        """A full URL passes through; a bare token is appended to the verify click-through base."""
        value = str(signed_link)
        if value.startswith(("http://", "https://")):
            return value
        if self.verify_url_base:
            return f"{self.verify_url_base.rstrip('/')}/{value}"
        return value  # no base configured — surfaces the raw token (still draft-gated)

    def _send(self, to_email: str, subject: str, html: str) -> bool:
        if not self.allow_real_sends:
            # DRAFT-GATE: refuse real delivery unless explicitly enabled.
            log.info("DRAFT-GATE: real sends disabled; dropping %r to %s", subject, to_email)
            return False
        if not self.api_key or not self.from_email:
            log.warning(
                "Resend unconfigured (missing api key / from address); dropping %r to %s",
                subject, to_email,
            )
            return False
        payload = {"from": self.from_email, "to": [to_email], "subject": subject, "html": html}
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            response = self._opener(request, _TIMEOUT_S)
            body = response.read()
            log.info("Resend accepted %r to %s: %s", subject, to_email, body[:200])
            return True
        except Exception as e:  # noqa: BLE001 — logs-not-raises by contract
            log.warning("Resend send failed for %s: %s: %s", to_email, type(e).__name__, e)
            return False
