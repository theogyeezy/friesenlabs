"""Server-side PostHog capture client for the signup funnel (TODO INT/P3).

The browser SDK can be ad-blocked; the funnel's MONEY events (payment_succeeded,
instance_provisioned, provisioning_failed) are therefore captured SERVER-side — this client is
what `api/prod_deps.py` injects into `signup.funnel.Funnel` when the PostHog key is configured
UNDER the SIGNUP_REAL_DEPS master switch (key value arrives via the NEW
`POSTHOG_PROJECT_KEY_VALUE` env name — Secrets Manager
`friesenlabs/platform/shared/posthog-project-key` is the source, infra/REQUESTS.md REQ-006).

Design constraints (all deliberate):
  * stdlib only (urllib) — no posthog SDK dependency in the API/Lambda images;
  * LAZY — construction touches no network, spawns no thread (cold-start safe);
  * NEVER blocks the request path — the default transport POSTs from a short-lived daemon
    thread (fire-and-forget; the webhook/provisioning caller never waits on PostHog);
  * NEVER raises — every failure (bad host, network, serialization) is swallowed and logged.
    Analytics must not be able to fail a payment webhook or park a provisioning run;
  * tenant grouping — a truthy `tenant_id` property is lifted into PostHog `$groups`
    (`{"tenant": <tenant_id>}`), and `group()` emits the `$groupidentify` event, so the funnel
    events roll up per tenant in PostHog group analytics.

Duck-type contract (what `signup.funnel.Funnel` calls on its injected client):
  capture(distinct_id, event, properties) + group(distinct_id, tenant_id).

The `transport` seam (callable(url, payload_dict)) exists for tests — and for a future batching
sender; with it injected, sends are synchronous through it (capture() still swallows anything it
raises). Single-event sends are fine at funnel volume (a handful of events per signup).
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# The PostHog group TYPE the funnel rolls up under (one group per tenant).
TENANT_GROUP_TYPE = "tenant"


class PostHogClient:
    def __init__(self, project_key: str, host: str = "https://us.i.posthog.com", *,
                 transport=None, timeout: float = 3.0):
        self._key = project_key
        self._endpoint = (host or "").rstrip("/") + "/capture/"
        self._transport = transport   # tests inject; None = urllib POST on a daemon thread
        self._timeout = timeout

    # ------------------------------------------------------------------ the Funnel contract
    def capture(self, distinct_id, event, properties=None) -> None:
        """Send one event. Never raises; never blocks the caller (module docstring)."""
        try:
            props = dict(properties or {})
            tenant_id = props.get("tenant_id")
            if tenant_id:
                # Group the event under its tenant (PostHog group analytics).
                props.setdefault("$groups", {TENANT_GROUP_TYPE: str(tenant_id)})
            payload = {
                "api_key": self._key,
                "event": str(event),
                "distinct_id": str(distinct_id),
                "properties": props,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._send(payload)
        except Exception:  # noqa: BLE001 — analytics may never break the request path
            log.warning("posthog capture failed for event %r (swallowed)", event, exc_info=True)

    def group(self, distinct_id, tenant_id) -> None:
        """Associate `distinct_id` with its tenant group (`Funnel.group_tenant`)."""
        self.capture(distinct_id, "$groupidentify", {
            "$group_type": TENANT_GROUP_TYPE,
            "$group_key": str(tenant_id),
            "tenant_id": str(tenant_id),   # lifted into $groups by capture()
        })

    # ------------------------------------------------------------------ delivery
    def _send(self, payload: dict) -> None:
        if self._transport is not None:
            # Injected transport (tests / a batching sender): synchronous; capture() already
            # swallows anything it raises.
            self._transport(self._endpoint, payload)
            return
        # Fire-and-forget: a short-lived daemon thread so the webhook/provisioning request
        # path never waits on PostHog (and a hung endpoint can never wedge shutdown).
        threading.Thread(target=self._post, args=(payload,),
                         name="posthog-capture", daemon=True).start()

    def _post(self, payload: dict) -> None:
        try:
            req = urllib.request.Request(
                self._endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                resp.read()
        except Exception as e:  # noqa: BLE001 — swallow-and-log (module docstring)
            log.warning("posthog delivery failed: %s: %s", type(e).__name__, e)
