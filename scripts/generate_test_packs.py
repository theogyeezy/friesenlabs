"""Reproducible test-data tiers ("scale packs") built on the demo-tenant generator.

This script WRAPS scripts/generate_demo_dataset.py — it never modifies it. It imports the
foundation generator's pure helpers, name/template pools, and output serializers and uses them
to emit three deterministic tiers to tests/fixtures/<tier>/:

  * smoke — 5 companies / 15 contacts / 8 deals. Tiny, fast: the fixture CI/unit tests load.
  * demo  — the ratified "Meridian Mechanical Group" shape (40/120/60 + 8 hero arcs, 14
            approvals, 2 saved views, ~450 activities/documents). Delegates verbatim to
            generate_demo_dataset.generate() so it stays byte-identical to the foundation.
  * load  — 2000 companies / 6000 contacts / 3000 deals for performance testing. Generation
            stays under 30s and the serialized JSON under 50MB (asserted in the test suite).

Determinism: each tier has a fixed seed and a fixed anchor date, so repeated runs are
byte-identical. The committed smoke + demo fixtures are drift-checked against a fresh
generation in tests/unit/test_generate_test_packs.py (and via `--check` here).

Fabrication discipline is inherited from the foundation and preserved at scale:
  * every email lives on an RFC 2606 `.example` domain — undeliverable by construction
  * every phone is in the NANP fictitious block `+1-<area>-555-01XX` (the load tier widens the
    area-code pool so the fictitious block still covers 6000 contacts with zero reuse)
  * every row carries `ref_id = demo:<entity>:<n>` — synthetic marker + idempotent upsert key

NO database connection. NO live mutation. stdlib-only (the foundation is too). File/stdout only.
The load tier is intentionally NOT committed (it would be ~20MB) — `tests/fixtures/load/` holds a
README + .gitignore; regenerate it on demand with `--tier load`.

Usage
-----
  python scripts/generate_test_packs.py --tier all          # write committed tiers
  python scripts/generate_test_packs.py --tier load         # regenerate the load pack locally
  python scripts/generate_test_packs.py --tier smoke --format json --out-dir /tmp/packs
  python scripts/generate_test_packs.py --check             # drift-check committed tiers (CI-safe)
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timedelta

# Import the foundation generator as a module — wrap, never edit it.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts import generate_demo_dataset as g  # noqa: E402

TEST_PACKS_VERSION = "1.0.0"
DEFAULT_OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures"
)

# Funnel weights mirrored from the ratified demo shape (14/12/11/8/9/6 = 60) so scaled tiers
# look like a real pipeline. Largest-remainder allocation keeps the per-stage counts summing
# exactly to the requested deal total.
FUNNEL_WEIGHTS = {
    "new": 14, "qualified": 12, "proposal": 11, "negotiation": 8,
    "closed_won": 9, "closed_lost": 6,
}

# Wide NANP-style area-code pool. The 555-0100..555-0199 block is fictitious in EVERY NPA, so
# more area codes just means more unique fictitious numbers (load needs >600 → >6 area codes).
# 80 DISTINCT codes x 100 = 8000 unique fictitious phones — enough for the load tier (needs 60
# blocks for 6000 contacts) with headroom. Distinctness is asserted below: a duplicate would
# shrink the effective pool and risk phone reuse past ~6400 contacts.
AREA_CODES_WIDE = (
    512, 737, 210, 830, 254, 361, 214, 469, 972, 281, 713, 832, 409, 936, 979, 940,
    817, 682, 430, 903, 325, 432, 806, 915, 956, 620, 316, 785, 913, 405, 539, 918,
    202, 305, 786, 407, 321, 561, 727, 813, 850, 904, 941, 239, 386, 352, 689, 754,
    404, 470, 678, 770, 706, 762, 478, 229, 912, 502, 859, 270, 615, 629, 901, 731,
    423, 865, 718, 314, 636, 660, 816, 417, 573, 845, 480, 602, 623, 520, 928, 343,
)
assert len(set(AREA_CODES_WIDE)) == len(AREA_CODES_WIDE), "area-code pool has duplicates"


def _scaled_phone(i: int) -> str:
    """Fictitious phone for index i, widening the area-code pool past the foundation's 600 cap."""
    area = AREA_CODES_WIDE[(i // 100) % len(AREA_CODES_WIDE)]
    return f"+1-{area}-555-01{i % 100:02d}"


def _allocate(total: int, weights: dict[str, int]) -> dict[str, int]:
    """Largest-remainder allocation of `total` across `weights`, summing exactly to total."""
    wsum = sum(weights.values())
    raw = {k: total * w / wsum for k, w in weights.items()}
    floors = {k: int(v) for k, v in raw.items()}
    used = sum(floors.values())
    # Distribute the remaining units to the largest fractional remainders (stable by key).
    remainders = sorted(weights, key=lambda k: (-(raw[k] - floors[k]), k))
    for k in remainders[: total - used]:
        floors[k] += 1
    return floors


def _tenant_for(tier: str) -> str:
    """Stable, distinct tenant uuid per tier (so a load pack never collides with smoke)."""
    return str(uuid.uuid5(g.DEMO_NAMESPACE, f"test-pack-tenant:{tier}"))


def build_scaled(tier: str, seed: int, n_companies: int, n_contacts: int, n_deals: int,
                 anchor_date: str = g.DEFAULT_ANCHOR_DATE) -> dict:
    """Build a scaled dataset dict in the SAME shape generate_demo_dataset.generate() returns.

    Reuses the foundation's pools/templates/helpers; supplies its own phone + domain logic so
    the fabrication discipline holds at arbitrary scale. Pure function of its arguments.
    """
    import random

    # Exact-total distribution requires >=1 contact per company and >=1 company.
    assert n_companies >= 1, "a tier needs at least one company"
    assert n_contacts >= n_companies, "n_contacts must be >= n_companies (>=1 contact each)"

    rng = random.Random(seed)
    tenant_id = _tenant_for(tier)
    anchor = datetime.fromisoformat(anchor_date).replace(hour=12, tzinfo=g.CENTRAL)

    companies: list[dict] = []
    contacts: list[dict] = []
    deals: list[dict] = []
    activities: list[dict] = []
    documents: list[dict] = []
    approvals: list[dict] = []

    segments = list(g.SEGMENTS)

    # --- companies -------------------------------------------------------------------------
    for n in range(n_companies):
        seg = segments[n % len(segments)]
        prefix = g.TAIL_NAME_PREFIXES[n % len(g.TAIL_NAME_PREFIXES)]
        suffix = rng.choice(g.SEGMENTS[seg]["suffixes"])
        name = f"{prefix} {suffix}"
        # Index-suffixed domain guarantees a unique, undeliverable .example domain at any scale.
        domain = f"{g._slug(name)}-{n}.example"
        companies.append({
            "id": g._uid(seed, f"{tier}:company:{n}"),
            "name": name,
            "domain": domain,
            "ref_id": f"demo:company:{n}",
            "created_at": g._iso(g._biz_time(rng, anchor - timedelta(days=rng.uniform(180, 900)))),
            "_segment": seg,
        })

    # --- contacts: distribute n_contacts across companies, >=1 each, exact total -----------
    per_company = [1] * n_companies
    for i in range(max(0, n_contacts - n_companies)):
        per_company[i % n_companies] += 1
    contact_n = 0
    contacts_by_company: dict[str, list[dict]] = {}
    for comp, k in zip(companies, per_company):
        used_here: set[str] = set()
        for _ in range(k):
            # Per-company name uniqueness is all that's needed: domains are globally unique,
            # so first.last@<unique-domain> never collides across the dataset.
            while True:
                full = f"{rng.choice(g.FIRST_NAMES)} {rng.choice(g.LAST_NAMES)}"
                if full not in used_here:
                    used_here.add(full)
                    break
            row = {
                "id": g._uid(seed, f"{tier}:contact:{contact_n}"),
                "company_id": comp["id"],
                "name": full,
                "email": g._email(full, comp["domain"]),
                "phone": _scaled_phone(contact_n),
                "ref_id": f"demo:contact:{contact_n}",
                "created_at": comp["created_at"],
            }
            contacts.append(row)
            contacts_by_company.setdefault(comp["id"], []).append(row)
            contact_n += 1

    # --- deals: distribute across companies (round-robin so coverage is even), exact total -
    stage_counts = _allocate(n_deals, FUNNEL_WEIGHTS)
    stage_list = [s for s, c in stage_counts.items() for _ in range(c)]
    rng.shuffle(stage_list)
    deal_n = 0
    for stage in stage_list:
        comp = companies[deal_n % n_companies]
        seg = comp["_segment"]
        contact = rng.choice(contacts_by_company[comp["id"]])
        kind = g._weighted(rng, g.TAIL_DEAL_KINDS)
        lo, hi, step = g.TAIL_AMOUNT_BANDS[kind]
        amount = int(round(rng.uniform(lo, hi) / step) * step)
        equipment, site = rng.choice(g.EQUIPMENT), rng.choice(g.SITES)
        title = rng.choice(g.TAIL_DEAL_TITLES[kind]).format(
            company=comp["name"], equipment=equipment, site=site)
        glo, ghi = g.STAGE_AGE_DAYS[stage]
        created = g._biz_time(rng, anchor - timedelta(days=rng.uniform(glo, ghi)))
        deals.append({
            "id": g._uid(seed, f"{tier}:deal:{deal_n}"),
            "company_id": comp["id"],
            "contact_id": contact["id"],
            "title": title,
            "stage": stage,
            "amount": amount,
            "currency": "USD",
            "ref_id": f"demo:deal:{deal_n}",
            "created_at": g._iso(created),
            "_segment": seg,
        })
        deal_n += 1

    contacts_by_id = {c["id"]: c for c in contacts}
    companies_by_id = {c["id"]: c for c in companies}

    # --- activities + mirrored documents (the foundation's templated path) -----------------
    act_n = 0
    for deal in deals:
        lo, hi = g.ACTIVITY_BAND[deal["stage"]]
        n = rng.randint(lo, hi)
        created = datetime.fromisoformat(deal["created_at"])
        if deal["stage"] in g.OPEN_STAGES:
            end = anchor - timedelta(days=rng.uniform(0.5, 3))
        else:
            end = created + (anchor - created) * rng.uniform(0.5, 0.85)
        span = end - created
        times = sorted(g._biz_time(rng, created + span * ((i + rng.uniform(0.2, 0.8)) / n))
                       for i in range(n))
        contact = contacts_by_id[deal["contact_id"]]
        equipment, site = rng.choice(g.EQUIPMENT), rng.choice(g.SITES)
        for when in times:
            kind = g._weighted(rng, g.KIND_WEIGHTS)
            body = rng.choice(g.KIND_TEMPLATES[kind]).format(
                contact=contact["name"], first=contact["name"].split()[0],
                company=companies_by_id[deal["company_id"]]["name"],
                equipment=equipment, site=site,
                segment_noun=g.SEGMENTS[deal["_segment"]]["noun"])
            activities.append({
                "id": g._uid(seed, f"{tier}:activity:{act_n}"),
                "contact_id": contact["id"],
                "deal_id": deal["id"],
                "kind": kind,
                "body": body,
                "occurred_at": g._iso(when),
            })
            documents.append({
                "source": g.ACTIVITY_DOC_SOURCE[kind],
                "ref_id": f"demo:doc:act:{len(documents)}",
                "content": f"{kind.title()} — {deal['title']} ({when.date().isoformat()}): {body}",
                "created_at": g._iso(when),
            })
            act_n += 1

    # --- a small, schema-valid approval set tied to real deals -----------------------------
    # Bounded: enough to exercise the Greenlight queue without ballooning the load pack.
    n_appr = min(len(deals), 6 if tier == "smoke" else 40)
    appr_deals = deals[:n_appr]
    for i, deal in enumerate(appr_deals):
        contact = contacts_by_id[deal["contact_id"]]
        status = "pending" if i % 3 else ("denied" if i % 6 == 0 and i else "approved")
        if status == "pending":
            created = anchor - timedelta(days=rng.uniform(0.2, 3))
            decided_at = decided_by = deny_message = None
        else:
            created = anchor - timedelta(days=rng.uniform(5, 45))
            decided_at = g._iso(created + timedelta(hours=rng.uniform(2, 30)))
            decided_by = "demo-admin"
            deny_message = "Below discount floor — needs VP sign-off." if status == "denied" else None
        approvals.append({
            "id": g._uid(seed, f"{tier}:approval:{i}"),
            "proposed_action": {
                "action": "send_email",
                "to": contact["email"],
                "deal_ref": deal["ref_id"],
                "deal": deal["title"],
                "subject": f"Follow-up — {deal['title']}",
                "body_preview": "Recap and next steps as discussed.",
            },
            "agent": "followup-agent",
            "reasoning": f"Follow-up queued for {deal['title']} ({deal['stage']}).",
            "value_at_stake": deal["amount"],
            "status": status,
            "decided_by": decided_by,
            "deny_message": deny_message,
            "created_at": g._iso(created),
            "decided_at": decided_at,
        })

    # --- saved views: reuse the foundation's two (already schema- + Cube-validated) --------
    saved_views = []
    for i, spec in enumerate(g.SAVED_VIEWS):
        saved_views.append({
            "id": g._uid(seed, f"{tier}:saved_view:{i}"),
            "view_id": spec["view_id"],
            "version": spec["version"],
            "spec_json": spec,
            "semantic_refs": spec["semantic_refs"],
            "source_prompt": spec["source_prompt"],
            "created_by": "test-pack-seed",
            "created_at": g._iso(anchor - timedelta(days=rng.uniform(1, 14))),
        })

    # Drop the internal-only `_segment` helper key before serialization (not a real column).
    for row in companies:
        row.pop("_segment", None)
    for row in deals:
        row.pop("_segment", None)

    return {
        "meta": {
            "generator": "scripts/generate_test_packs.py",
            "version": TEST_PACKS_VERSION,
            "tier": tier,
            "seed": seed,
            "anchor_date": anchor_date,
            "tenant_id": tenant_id,
            "decision_brief": g.DECISION_BRIEF,
            "ratification": g.RATIFICATION,
            "test_pack": True,
            "counts": {
                "companies": len(companies), "contacts": len(contacts), "deals": len(deals),
                "activities": len(activities), "approvals": len(approvals),
                "saved_views": len(saved_views), "documents": len(documents),
            },
        },
        "companies": companies,
        "contacts": contacts,
        "deals": deals,
        "activities": activities,
        "approvals": approvals,
        "saved_views": saved_views,
        "documents": documents,
    }


# Tier registry. The `demo` tier delegates to the foundation so it stays byte-identical.
TIERS = {
    "smoke": {"seed": 11, "n_companies": 5, "n_contacts": 15, "n_deals": 8, "commit": True},
    "demo": {"seed": g.DEFAULT_SEED, "commit": True},  # delegates to generate_demo_dataset.generate()
    "load": {"seed": 97, "n_companies": 2000, "n_contacts": 6000, "n_deals": 3000, "commit": False},
}


def build_tier(tier: str) -> dict:
    """Return the dataset dict for a tier (demo delegates to the ratified foundation)."""
    cfg = TIERS[tier]
    if tier == "demo":
        return g.generate()  # seed 47, default tenant, default anchor — the ratified shape
    return build_scaled(tier, cfg["seed"], cfg["n_companies"], cfg["n_contacts"], cfg["n_deals"])


def _write_tier(tier: str, out_dir: str, fmt: str) -> dict:
    dataset = build_tier(tier)
    tier_dir = os.path.join(out_dir, tier)
    os.makedirs(tier_dir, exist_ok=True)
    if fmt in ("json", "both"):
        with open(os.path.join(tier_dir, "dataset.json"), "w", encoding="utf-8") as f:
            f.write(g.to_json(dataset))
    if fmt in ("sql", "both"):
        with open(os.path.join(tier_dir, "dataset.sql"), "w", encoding="utf-8") as f:
            f.write(g.to_sql(dataset))
    return dataset["meta"]["counts"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate reproducible test-data tiers (smoke/demo/load) from the demo "
                    "generator. NO database connection — file output only.")
    parser.add_argument("--tier", choices=("smoke", "demo", "load", "all"), default="all",
                        help="which tier to emit (default: all committed tiers + load)")
    parser.add_argument("--format", choices=("json", "sql", "both"), default="both")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                        help=f"fixtures root (default {DEFAULT_OUT_DIR})")
    parser.add_argument("--check", action="store_true",
                        help="regenerate committed tiers in memory and verify they match the "
                             "committed fixtures byte-for-byte; exit 1 on drift")
    args = parser.parse_args(argv)

    if args.check:
        drift = []
        for tier, cfg in TIERS.items():
            if not cfg["commit"]:
                continue
            path = os.path.join(args.out_dir, tier, "dataset.json")
            if not os.path.exists(path):
                drift.append(f"{tier}: missing {path}")
                continue
            with open(path, encoding="utf-8") as f:
                committed = f.read()
            if committed != g.to_json(build_tier(tier)):
                drift.append(f"{tier}: dataset.json drifted from generation")
        if drift:
            sys.stderr.write("DRIFT:\n  " + "\n  ".join(drift) + "\n")
            return 1
        sys.stderr.write("ok: committed tiers match generation\n")
        return 0

    tiers = ("smoke", "demo", "load") if args.tier == "all" else (args.tier,)
    for tier in tiers:
        counts = _write_tier(tier, args.out_dir, args.format)
        sys.stderr.write(f"wrote {os.path.join(args.out_dir, tier)} ({tier}): {counts}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
