"""Slot resolution — the NL-to-governed-call bridge (Build Guide Step 36).

`resolve_slots(text, ctx)` turns human references into the system IDs/values a governed call needs:
  - "Acme account" -> company_id        (via the injected tenant-scoped CRM client)
  - a contact name  -> contact_id       (via the injected tenant-scoped CRM client)
  - "last quarter" / "this month" -> a concrete (start, end) date range
  - "Riverside" / a region word -> a Cube dimension value (via the cube dimension catalog)

Hard rules:
  - NEVER silently guess across ambiguous matches. On >1 candidate, return a `Disambiguation`
    (candidates + a prompt). An injected `disambiguator` may pick *only* when it returns high
    confidence; otherwise we surface the choice to the human.
  - Date math is deterministic and testable: `ctx.today` is passed in. We never read the clock here.
  - Every lookup is tenant-scoped: it goes through the injected, tenant-bound clients (RLS/Cube
    context). We never write a tenant filter by hand.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol


# --------------------------------------------------------------------------- clients (injected)
class CrmLookup(Protocol):
    """Tenant-bound CRM read client. Implementations resolve a human name to entity rows already
    scoped to the tenant (RLS). They MUST NOT accept cross-tenant input."""

    def find_companies(self, tenant_id: str, name: str) -> list[dict]: ...
    def find_contacts(self, tenant_id: str, name: str) -> list[dict]: ...


class CubeCatalog(Protocol):
    """Tenant-bound Cube client exposing the dimension catalog (distinct values per dimension)."""

    def dimension_values(self, tenant_id: str, dimension: str) -> list[str]: ...


class Disambiguator(Protocol):
    """An injected LLM/heuristic that MAY pick among candidates when confident.

    Returns either {"index": int, "confidence": float} or {"confidence": float}. We only honor a
    pick when confidence >= the configured threshold; otherwise we surface a Disambiguation.
    """

    def pick(self, *, text: str, slot: str, candidates: list[dict]) -> dict: ...


# --------------------------------------------------------------------------- context + results
@dataclass
class SlotContext:
    """Per-resolution context. Carries the tenant + injected tenant-bound clients + `today`."""

    tenant_id: str
    today: date                      # injected for deterministic date math — never read the clock
    crm: CrmLookup | None = None
    cube: Any = None                 # exposes dimension_values(tenant_id, dimension)
    disambiguator: Disambiguator | None = None
    # which Cube dimensions we will scan for a free-text dimension value (e.g. region).
    dimension_catalog: list[str] = field(default_factory=lambda: ["region"])
    confidence_threshold: float = 0.85


@dataclass
class Candidate:
    """One possible resolution for a slot."""

    value: Any
    label: str
    meta: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"value": self.value, "label": self.label, **({"meta": self.meta} if self.meta else {})}


@dataclass
class Disambiguation:
    """Returned for a slot when >1 candidate matched and we will NOT guess. The caller must surface
    `prompt` + `candidates` to the human (or feed a confident disambiguator)."""

    slot: str
    text: str
    candidates: list[Candidate]
    prompt: str

    def as_dict(self) -> dict:
        return {
            "type": "disambiguation",
            "slot": self.slot,
            "text": self.text,
            "prompt": self.prompt,
            "candidates": [c.as_dict() for c in self.candidates],
        }


@dataclass
class ResolvedSlots:
    """The structured output of slot resolution.

    `slots` holds cleanly-resolved values (exactly one match). `ambiguous` holds the slots that
    matched more than one candidate — each a `Disambiguation` to surface. `unresolved` lists slots
    whose reference was detected but matched nothing.
    """

    slots: dict[str, Any] = field(default_factory=dict)
    ambiguous: list[Disambiguation] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    @property
    def needs_disambiguation(self) -> bool:
        return bool(self.ambiguous)


# --------------------------------------------------------------------------- date phrases
def _quarter_bounds(year: int, q: int) -> tuple[date, date]:
    start_month = 3 * (q - 1) + 1
    end_month = start_month + 2
    last_day = calendar.monthrange(year, end_month)[1]
    return date(year, start_month, 1), date(year, end_month, last_day)


def _prev_quarter(today: date) -> tuple[date, date]:
    q = (today.month - 1) // 3 + 1
    if q == 1:
        return _quarter_bounds(today.year - 1, 4)
    return _quarter_bounds(today.year, q - 1)


def _this_quarter(today: date) -> tuple[date, date]:
    q = (today.month - 1) // 3 + 1
    return _quarter_bounds(today.year, q)


def _this_month(today: date) -> tuple[date, date]:
    last = calendar.monthrange(today.year, today.month)[1]
    return date(today.year, today.month, 1), date(today.year, today.month, last)


def _last_month(today: date) -> tuple[date, date]:
    year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _this_year(today: date) -> tuple[date, date]:
    return date(today.year, 1, 1), date(today.year, 12, 31)


def _last_year(today: date) -> tuple[date, date]:
    return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)


def _ytd(today: date) -> tuple[date, date]:
    return date(today.year, 1, 1), today


# Ordered: more specific phrases first so "last quarter" doesn't match a bare "quarter" rule.
_DATE_PHRASES: list[tuple[re.Pattern, Any]] = [
    (re.compile(r"\blast\s+quarter\b", re.I), _prev_quarter),
    (re.compile(r"\bprevious\s+quarter\b", re.I), _prev_quarter),
    (re.compile(r"\bthis\s+quarter\b", re.I), _this_quarter),
    (re.compile(r"\bcurrent\s+quarter\b", re.I), _this_quarter),
    (re.compile(r"\blast\s+month\b", re.I), _last_month),
    (re.compile(r"\bthis\s+month\b", re.I), _this_month),
    (re.compile(r"\bcurrent\s+month\b", re.I), _this_month),
    (re.compile(r"\byear[\s-]*to[\s-]*date\b", re.I), _ytd),
    (re.compile(r"\bytd\b", re.I), _ytd),
    (re.compile(r"\blast\s+year\b", re.I), _last_year),
    (re.compile(r"\bthis\s+year\b", re.I), _this_year),
]


def resolve_date_range(text: str, today: date) -> dict | None:
    """Deterministic NL date phrase -> {'start','end'} (ISO strings). `today` is injected.

    Returns None if no known phrase is present. First match wins (phrases are ordered specific-first).
    """
    for pattern, fn in _DATE_PHRASES:
        if pattern.search(text):
            start, end = fn(today)
            return {"start": start.isoformat(), "end": end.isoformat(), "phrase": pattern.pattern}
    return None


# --------------------------------------------------------------------------- entity references
# "Acme account" / "the Acme company" -> name="Acme"; "contact <Name>" -> contact name.
_COMPANY_REF = re.compile(r"\b(?:the\s+)?([A-Z][\w&.\- ]*?)\s+(?:account|company|corp|inc|org)\b")
_CONTACT_REF = re.compile(
    r"\b(?:contact|person|reach out to|email)\s+([A-Z][\w.\-']*(?:\s+[A-Z][\w.\-']*){0,2})"
)


def _prompt_for(slot: str, text: str, candidates: list[Candidate]) -> str:
    opts = "; ".join(f"{i + 1}) {c.label}" for i, c in enumerate(candidates))
    return f"Which {slot} did you mean by that? Options: {opts}"


def _resolve_one(
    ctx: SlotContext,
    *,
    slot: str,
    text: str,
    candidates: list[Candidate],
) -> tuple[str, Any] | Disambiguation | None:
    """Apply the never-guess rule.

    0 candidates -> None (unresolved). 1 -> (slot, value). >1 -> let a *confident* disambiguator
    pick, else return a Disambiguation. We never silently choose among several.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return slot, candidates[0].value

    # Multiple matches. Offer them to an injected disambiguator — honor a pick ONLY if confident.
    if ctx.disambiguator is not None:
        choice = ctx.disambiguator.pick(
            text=text, slot=slot, candidates=[c.as_dict() for c in candidates]
        )
        idx = choice.get("index")
        conf = float(choice.get("confidence", 0.0))
        if idx is not None and conf >= ctx.confidence_threshold and 0 <= idx < len(candidates):
            return slot, candidates[idx].value

    return Disambiguation(
        slot=slot, text=text, candidates=candidates, prompt=_prompt_for(slot, text, candidates)
    )


def _company_candidates(ctx: SlotContext, name: str) -> list[Candidate]:
    if ctx.crm is None:
        return []
    rows = ctx.crm.find_companies(ctx.tenant_id, name) or []
    return [
        Candidate(value=r["id"], label=r.get("name", name), meta={"domain": r.get("domain")})
        for r in rows
    ]


def _contact_candidates(ctx: SlotContext, name: str) -> list[Candidate]:
    if ctx.crm is None:
        return []
    rows = ctx.crm.find_contacts(ctx.tenant_id, name) or []
    return [
        Candidate(value=r["id"], label=r.get("name", name), meta={"email": r.get("email")})
        for r in rows
    ]


def _dimension_candidates(ctx: SlotContext, text: str) -> list[Candidate]:
    """Scan the configured Cube dimensions for a value mentioned (case-insensitively) in `text`."""
    if ctx.cube is None:
        return []
    found: list[Candidate] = []
    seen: set[tuple[str, str]] = set()
    lowered = text.lower()
    for dim in ctx.dimension_catalog:
        try:
            values = ctx.cube.dimension_values(ctx.tenant_id, dim) or []
        except Exception:
            values = []
        for v in values:
            if v and re.search(rf"\b{re.escape(str(v).lower())}\b", lowered):
                key = (dim, str(v))
                if key not in seen:
                    seen.add(key)
                    found.append(Candidate(value={"dimension": dim, "value": v}, label=f"{dim}={v}"))
    return found


def resolve_slots(text: str, ctx: SlotContext) -> ResolvedSlots:
    """Resolve every slot we can detect in `text`, tenant-scoped, never guessing on ambiguity.

    Detected slots (independently): date_range, company_id, contact_id, dimension. Each clean match
    lands in `.slots`; each ambiguous match lands in `.ambiguous` as a Disambiguation; a detected-but-
    unmatched reference lands in `.unresolved`.
    """
    out = ResolvedSlots()

    # --- date range (deterministic, uses injected today) ---
    dr = resolve_date_range(text, ctx.today)
    if dr is not None:
        out.slots["date_range"] = dr

    # --- company_id ---
    cm = _COMPANY_REF.search(text)
    if cm:
        name = cm.group(1).strip()
        res = _resolve_one(ctx, slot="company_id", text=text, candidates=_company_candidates(ctx, name))
        if res is None:
            out.unresolved.append("company_id")
        elif isinstance(res, Disambiguation):
            out.ambiguous.append(res)
        else:
            out.slots[res[0]] = res[1]

    # --- contact_id ---
    ct = _CONTACT_REF.search(text)
    if ct:
        name = ct.group(1).strip()
        res = _resolve_one(ctx, slot="contact_id", text=text, candidates=_contact_candidates(ctx, name))
        if res is None:
            out.unresolved.append("contact_id")
        elif isinstance(res, Disambiguation):
            out.ambiguous.append(res)
        else:
            out.slots[res[0]] = res[1]

    # --- cube dimension value (e.g. region "Riverside") ---
    dim_cands = _dimension_candidates(ctx, text)
    res = _resolve_one(ctx, slot="dimension", text=text, candidates=dim_cands)
    if isinstance(res, Disambiguation):
        out.ambiguous.append(res)
    elif res is not None:
        out.slots[res[0]] = res[1]

    return out
