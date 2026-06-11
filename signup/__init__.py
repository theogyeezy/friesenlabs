"""Signup package — plus the shared PII log-masking helpers the outbound senders use.

WHY HERE: both senders (resend_sender / sms_sender) were logging the FULL recipient address /
phone number on every gated, accepted, and failed send — turning CloudWatch into an unguarded
PII store (emails + phones of every signup, retained on the log group's schedule, readable by
anyone with log access). These helpers mask before logging; the package __init__ is the one
natural home BOTH senders can import without minting a new module. stdlib-only and side-effect
free — importing `signup` stays cheap and safe.

Contract: NEVER log a raw email or phone — log `mask_email(...)` / `mask_phone(...)` instead.
The masked forms keep just enough signal to correlate a support ticket (first letter + domain;
last 4 digits) without reconstructing the identifier. Both helpers are total: junk/None input
yields a masked placeholder, never an exception (a logging helper must never break a send path).
"""
from __future__ import annotations


def mask_email(email: object) -> str:
    """`john@acme.com` -> `j***@acme.com`. The domain stays (ops signal: which mail provider /
    tenant domain); the local part is reduced to its first character. Junk in, `***` out."""
    s = str(email or "")
    if not s:
        return "***"
    if "@" not in s:
        return f"{s[:1]}***"
    local, _, domain = s.partition("@")
    return f"{local[:1]}***@{domain}"


def mask_phone(phone: object) -> str:
    """`+15555550100` -> `***0100` (last 4 only — the usual support-correlation form).
    Anything shorter than 5 characters masks entirely. Junk in, `***` out."""
    s = str(phone or "")
    if len(s) <= 4:
        return "***"
    return f"***{s[-4:]}"
