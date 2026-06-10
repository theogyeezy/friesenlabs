"""Full-funnel tracking (Build Guide Phase 10, Step 56).

The signup funnel: landing_view -> signup_started -> email_verified -> phone_verified ->
payment_submitted -> payment_succeeded -> instance_provisioned -> first_login — plus the
terminal-failure branch `provisioning_failed` (emitted by Provisioner.park_failed: a charged
customer whose instance never came up is the funnel's most expensive drop-off). Revenue events are
captured SERVER-side (from the Stripe webhook) so ad-blockers can't drop them. The PostHog client is
injected (prod: signup/posthog_client.PostHogClient); tests use a recorder.
"""
from __future__ import annotations

FUNNEL = [
    "landing_view", "signup_started", "email_verified", "phone_verified",
    "payment_submitted", "payment_succeeded", "instance_provisioned", "provisioning_failed",
    "first_login",
]


class Funnel:
    def __init__(self, posthog):
        self.posthog = posthog  # injected: capture(distinct_id, event, properties), group(tenant)

    def capture(self, distinct_id: str, event: str, **properties) -> None:
        if event not in FUNNEL:
            raise ValueError(f"unknown funnel event {event!r}")
        self.posthog.capture(distinct_id, event, properties)

    def revenue(self, account_id: str, plan: str, mrr: float) -> None:
        # Server-side revenue truth (from the webhook), grouped per tenant later at provisioning.
        self.posthog.capture(account_id, "payment_succeeded", {"plan": plan, "mrr": mrr})

    def group_tenant(self, account_id: str, tenant_id: str) -> None:
        self.posthog.group(account_id, tenant_id)
