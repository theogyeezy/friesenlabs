"""Greenlight approval appliers.

Side-effecting tools are draft-only: invoking them queues proposals and never mutates external
systems. This module is the post-approval dispatch surface that turns an approved CRM proposal into
the corresponding tenant-scoped write. Email/quote providers are explicitly record-only here.
"""
from __future__ import annotations

from typing import Any, Callable


ApplyResult = dict[str, Any]
Applier = Callable[[Any, str, dict], ApplyResult]

RECORD_ONLY_RESULT = {
    "performed": False,
    "reason": "draft-only until provider go-live",
}


def _require_crm(crm: Any) -> Any:
    if crm is None:
        raise RuntimeError("CRM client not configured")
    return crm


def apply_update_deal(crm: Any, tenant_id: str, payload: dict) -> ApplyResult:
    client = _require_crm(crm)
    deal_id = payload["deal_id"]
    changes = payload.get("changes") or {}
    result = client.update_deal_fields(
        tenant_id=tenant_id, deal_id=str(deal_id), changes=changes
    )
    return {
        "performed": True,
        "action": "update_deal",
        "deal_id": str(deal_id),
        "result": result,
    }


def apply_update_contact(crm: Any, tenant_id: str, payload: dict) -> ApplyResult:
    client = _require_crm(crm)
    contact_id = payload["contact_id"]
    changes = payload.get("changes") or {}
    result = client.update_contact_fields(
        tenant_id=tenant_id, contact_id=str(contact_id), changes=changes
    )
    return {
        "performed": bool(result.get("updated")),
        "action": "update_contact",
        "contact_id": str(contact_id),
        "result": result,
    }


def apply_create_activity(crm: Any, tenant_id: str, payload: dict) -> ApplyResult:
    client = _require_crm(crm)
    result = client.insert_activity(
        tenant_id=tenant_id,
        contact_id=payload.get("contact_id"),
        deal_id=payload.get("deal_id"),
        kind=payload["kind"],
        body=payload["body"],
    )
    return {"performed": True, "action": "create_activity", "result": result}


def apply_create_deal(crm: Any, tenant_id: str, payload: dict) -> ApplyResult:
    client = _require_crm(crm)
    result = client.insert_deal(
        tenant_id=tenant_id,
        company_id=str(payload["company_id"]),
        name=payload["name"],
        stage=payload["stage"],
        amount=payload.get("amount"),
        contact_id=payload.get("contact_id"),
    )
    return {"performed": True, "action": "create_deal", "result": result}


def record_only(_crm: Any, _tenant_id: str, _payload: dict) -> ApplyResult:
    return dict(RECORD_ONLY_RESULT)


APPLIERS: dict[str, Applier] = {
    "update_deal": apply_update_deal,
    "update_contact": apply_update_contact,
    "create_activity": apply_create_activity,
    "create_deal": apply_create_deal,
    "send_email": record_only,
    "issue_quote": record_only,
}


def apply_approved_action(crm: Any, tenant_id: str, payload: dict) -> ApplyResult:
    action = payload.get("action")
    applier = APPLIERS.get(action)
    if applier is None:
        raise ValueError(f"no applier registered for action {action!r}")
    return applier(crm, tenant_id, payload)
