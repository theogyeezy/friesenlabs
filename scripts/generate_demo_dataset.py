"""Synthetic demo-tenant dataset generator — "Meridian Mechanical Group".

DO NOT WIRE INTO PROVISIONING UNTIL RATIFIED: implements the spec in
docs/decisions/demo-tenant-synthetic-dataset.md, which is PENDING ratification (issue #123).
The generator itself is side-effect-free (it only writes a file / stdout), so landing it is
safe; *using* its output against the live demo tenant is gated on the brief being ratified.

What it does
------------
Deterministically fabricates the demo tenant's CRM universe for a fictional Austin commercial
HVAC/plumbing services firm: ~40 companies, 120 contacts, 60 deals across a realistic funnel,
~450 backdated activity narratives, 6 Greenlight-able pending approvals designed against the L2
autonomy thresholds ($1,000 / 10% — api/control/autonomy.py), 8 decided approvals so the queue
has history, 2 saved views that validate against shared/schemas/view_spec.schema.json using
real Cube members (semantic/model/cubes/), and a documents corpus (every activity mirrored +
8 longer authored docs) ready for a load-time embed pass.

Eight "hero arcs" are hand-authored data constants with retrievable story beats (names,
numbers, objections, dates) — these are what the demo script's RAG questions hit. The ~32
company tail is template-generated so dashboards look like a real business.

Fabrication discipline (non-negotiable, per the brief):
  * every email/domain is `<slug>.example` — RFC 2606-reserved TLD, undeliverable by construction
  * every phone is in the NANP fictitious block `+1-<area>-555-01XX`, zero reuse
  * brands/competitors fabricated (Apex Air Systems, NorthCool, Kestrel, ...); zero real PII
  * every row carries `ref_id = demo:<entity>:<n>` (synthetic marker + idempotent upsert key)

NO live DB connection — this script ONLY generates files. stdlib-only (random/json/datetime/
uuid/argparse): no boto3, no psycopg2, no faker.

Usage
-----
  python scripts/generate_demo_dataset.py --seed 47 --format json --out seed_data/demo_tenant.json
  python scripts/generate_demo_dataset.py --format sql --tenant <uuid> --out demo_tenant.sql

`--format sql` emits one idempotent transaction meant to run as the RLS-bound `crm_app` role
(SET LOCAL app.current_tenant): wipe-then-insert for the CRM tables (the seed_demo_tenant.py
pattern), `documents` wiped only where `ref_id LIKE 'demo:%'`. The `documents.embedding`
column is left NULL — a load-time embed pass (Titan V2 via the ingest embedder seam) must
backfill it before RAG retrieval works.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

GENERATOR = "scripts/generate_demo_dataset.py"
GENERATOR_VERSION = "1.0.0"
DECISION_BRIEF = "docs/decisions/demo-tenant-synthetic-dataset.md"
RATIFICATION = "PENDING — do not load against the live demo tenant until issue #123 ratifies the brief"

DEFAULT_SEED = 47
DEFAULT_ANCHOR_DATE = "2026-06-09"  # fixed so output is byte-identical run-to-run

# Deterministic UUID namespace for every generated id (uuid5 = stable across runs).
DEMO_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "uplift://demo-tenant-dataset")
DEFAULT_TENANT_ID = str(uuid.uuid5(DEMO_NAMESPACE, "tenant:meridian-mechanical-demo"))

CENTRAL = timezone(timedelta(hours=-5))  # US Central (daylight)

STAGES = ("new", "qualified", "proposal", "negotiation", "closed_won", "closed_lost")
OPEN_STAGES = ("new", "qualified", "proposal", "negotiation")

# Funnel totals (brief table): 14/12/11/8/9/6 = 60.
TAIL_STAGE_COUNTS = {  # 52 tail deals; heroes fill the other 8 slots (see HERO_ARCS stages)
    "new": 14, "qualified": 11, "proposal": 9, "negotiation": 5,
    "closed_won": 8, "closed_lost": 5,
}

# Per-deal activity count band by stage (brief): new 2-3, qualified 4-6, proposal 6-9,
# negotiation 8-12, closed 10-14.
ACTIVITY_BAND = {
    "new": (2, 3), "qualified": (4, 6), "proposal": (6, 9), "negotiation": (8, 12),
    "closed_won": (10, 14), "closed_lost": (10, 14),
}

# Deal age (days before anchor) by stage — older the deeper in the funnel.
STAGE_AGE_DAYS = {
    "new": (3, 21), "qualified": (14, 45), "proposal": (30, 75), "negotiation": (45, 100),
    "closed_won": (60, 150), "closed_lost": (60, 150),
}

AREA_CODES = (512, 737, 210, 830, 254, 361)  # x 555-01XX = 600 unique fictitious numbers

ROLES = ("Facilities Director", "Chief Engineer", "Director of Ops",
         "Property Manager", "CFO/Controller", "Procurement Manager")

# Curated synthetic name pools — common-combination fabrication (the deliverability boundary
# is domains/phones; see the brief). No real person is referenced.
FIRST_NAMES = (
    "Adriana", "Bennett", "Calliope", "Dario", "Elias", "Farrah", "Gideon", "Harriet",
    "Imogen", "Jericho", "Katya", "Lamont", "Maribel", "Nico", "Odessa", "Porter",
    "Quinlan", "Rosalind", "Stellan", "Tovah", "Ulysses", "Vada", "Wendell", "Ximena",
    "Yusuf", "Zelda", "Ansel", "Birdie", "Caspian", "Delphine", "Emerson", "Fletcher",
    "Greer", "Hollis", "Ingrid", "Jules", "Kerensa", "Lazlo", "Marguerite", "Nadia",
)
LAST_NAMES = (
    "Ashcombe", "Bellweather", "Carrasco", "Dunmore", "Eastvale", "Fenwick", "Gallardo",
    "Hollenbeck", "Irastorza", "Jurgens", "Kettleman", "Larrabee", "Montufar", "Northcutt",
    "Oberlin", "Pellegrew", "Quintanar", "Rookwood", "Sandoval", "Thackeray", "Umbarger",
    "Vanterpool", "Wexford", "Yarbrough", "Zellerbach", "Ardenwood", "Briarcliff",
    "Castellanos", "Dunsmuir", "Elkington", "Featherston", "Granbury", "Holloway",
    "Inverness", "Joffrion", "Kirkbride", "Loxley", "Marwood", "Nethercott", "Ostrander",
)

# Fabricated equipment + sites for templated narratives (no real vendors).
EQUIPMENT = (
    "NorthCool C-450 chiller", "Kestrel KX-90 RTU", "TruFlow TF-8 boiler",
    "AeroVent AV-2 makeup-air unit", "ClimaCore CC-30 cooling tower",
    "Permafrost PF-9 walk-in unit",
)
SITES = (
    "the north campus", "building B", "the main plant", "suite 400",
    "the distribution floor", "the rooftop penthouse", "the south annex", "the central plant",
)

SEGMENTS = {
    "property_mgmt": {"tail": 8, "noun": "property-management",
                      "suffixes": ("Property Group", "Realty Management", "Commons Management")},
    "industrial": {"tail": 5, "noun": "logistics",
                   "suffixes": ("Logistics", "Distribution Co", "Freight Systems")},
    "healthcare": {"tail": 4, "noun": "healthcare",
                   "suffixes": ("Medical Plaza", "Health Partners", "Surgical Associates")},
    "hospitality": {"tail": 4, "noun": "hospitality",
                    "suffixes": ("Hospitality Group", "Hotel Collective", "Restaurant Group")},
    "office_reit": {"tail": 4, "noun": "commercial-office",
                    "suffixes": ("Tower Holdings", "Workspace Trust", "Office Partners")},
    "education": {"tail": 3, "noun": "school-district",
                  "suffixes": ("ISD", "Academy", "Charter Schools")},
    "municipal": {"tail": 4, "noun": "municipal",
                  "suffixes": ("Utility District", "Parks Conservancy", "Community Foundation")},
}

TAIL_NAME_PREFIXES = (
    "Lakeline", "Barton Springs", "Pflugerville", "Round Rock", "Cedar Bend", "Onion Creek",
    "Walnut Hollow", "Shoal Crossing", "Bluff View", "Saltgrass", "Yaupon", "Kingfisher",
    "Granite Knoll", "Whitestone", "Bee Cave", "Brushy Bend", "Palomino", "Cypress Gate",
    "Redbud", "Quarry Lake", "Silverthorn", "Hutto Ridge", "Mesquite Flats", "Caliche",
    "Loblolly", "Chisholm", "Wildhorse", "Longhorn Bend", "Prairie Gate", "Hackberry",
    "Twin Buttes", "Sendero",
)

# Tail-deal shape: (kind, weight). Amount bands per kind keep open pipeline ~ $2.1M.
TAIL_DEAL_KINDS = (("repair", 0.475), ("service", 0.475), ("install", 0.05))
TAIL_AMOUNT_BANDS = {"repair": (4500, 18000, 50), "service": (18000, 72000, 500),
                     "install": (80000, 220000, 1000)}
TAIL_DEAL_TITLES = {
    "repair": ("{company} — {equipment} repair", "{company} emergency repair — {site}",
               "{company} — compressor replacement, {equipment}"),
    "service": ("{company} service agreement renewal FY27", "{company} quarterly PM contract",
                "{company} preventive-maintenance agreement",
                "{company} comfort-cooling service plan"),
    "install": ("{company} RTU replacement — {site}", "{company} boiler retrofit",
                "{company} BAS upgrade — {site}"),
}

CALL_TEMPLATES = (
    "Spoke with {contact} about the {equipment} at {site}; agreed to schedule a diagnostic visit next week.",
    "{first} called about uneven temperatures at {site}; suspect a stuck damper actuator. Visit scheduled.",
    "Checked in with {contact} ahead of the seasonal changeover; confirmed access windows for {site}.",
    "{first} reported a noise complaint near the {equipment}; likely a worn belt. Tech routed for Thursday.",
    "Follow-up call with {contact}: parts for the {equipment} arrived; install set for early next week.",
    "Call recap: {contact} asked for references from other {segment_noun} accounts before moving ahead.",
)
EMAIL_TEMPLATES = (
    "Sent {first} the service summary for {site}: filters changed, belts inspected, no abnormal readings on the {equipment}.",
    "Emailed {contact} the maintenance-window options for {site}; awaiting their preferred slot.",
    "Sent the inspection report for the {equipment} with photos and a parts list for the worn components.",
    "Recap email to {contact}: scope, exclusions, and response-time commitments as discussed.",
    "Sent {first} the updated certificate of insurance and W-9 for their vendor file.",
)
NOTE_TEMPLATES = (
    "Tech report: {equipment} at {site} back within spec after coil cleaning; added to the quarterly PM route.",
    "Site note: roof access at {site} requires 24-hour notice and a badge escort — logged for dispatch.",
    "Internal: {company} pays net-30 reliably; good candidate for the annual-agreement upsell next quarter.",
    "Diagnostic: refrigerant charge low on the {equipment}; leak search scheduled before any top-off.",
    "Logged warranty terms for the {equipment} — compressor covered through next year.",
)
MEETING_TEMPLATES = (
    "Quarterly review with {contact}: walked open tickets, uptime stats, and the {equipment} replacement timeline.",
    "On-site scoping at {site} with {contact}; measured electrical capacity for the proposed {equipment}.",
    "Walkthrough with {contact} and their engineer at {site}; documented unit ages and refrigerant types.",
    "Intro meeting with {contact}: mapped their sites, current vendor gaps, and budget cycle.",
)
KIND_TEMPLATES = {"call": CALL_TEMPLATES, "email": EMAIL_TEMPLATES,
                  "note": NOTE_TEMPLATES, "meeting": MEETING_TEMPLATES}
KIND_WEIGHTS = (("call", 0.30), ("email", 0.30), ("note", 0.25), ("meeting", 0.15))

# documents.source vocabulary stays inside the schema's comment (hubspot|stripe|call|email|upload)
ACTIVITY_DOC_SOURCE = {"call": "call", "email": "email", "note": "upload", "meeting": "upload"}


# ---------------------------------------------------------------------------
# THE 8 HERO ARCS — hand-authored story beats (the brief's table, verbatim shapes).
# Beats are chronological (kind, body). All names/brands fabricated.
# ---------------------------------------------------------------------------
HERO_ARCS = (
    {
        "key": "westlake",
        "company": {"name": "Pinnacle Property Partners", "segment": "property_mgmt"},
        "contacts": (("Rosa Camarillo", "Facilities Director"), ("Emory Voss", "Chief Engineer")),
        "deal": {"title": "Westlake Galleria chiller retrofit", "stage": "negotiation", "amount": 284000},
        "beats": (
            ("call", "Emergency call from Rosa Camarillo: chiller #2 (NorthCool C-450) at the Westlake Galleria tripped on high head pressure during the afternoon peak. Tenants on floors 4-6 reporting 81F. Dispatched the on-call tech."),
            ("note", "Field report, Westlake Galleria: C-450 chiller #2 compressor B is down — scored crankshaft, metal in the oil. The temporary repair holds for the season, but both C-450s are 17 years old and on R-22. Recommended a full retrofit over another compressor swap."),
            ("email", "Sent Rosa the emergency-repair summary plus a preliminary retrofit budget range of $260-300K for replacing both C-450 chillers with magnetic-bearing units."),
            ("meeting", "Retrofit walkthrough with Rosa Camarillo and chief engineer Emory Voss: two high-efficiency magnetic-bearing chillers, BAS integration, a Saturday crane day, and a phased cutover so the galleria never loses cooling."),
            ("email", "Proposal v2 sent to Rosa: $284,000 total, 14-week equipment lead time, phased cutover plan attached."),
            ("call", "Rosa: the Pinnacle property board meets on the 24th. They will not sign until we provide a COI naming Pinnacle Property Partners as additional insured."),
            ("note", "Open items on Westlake: board approval pending, COI request outstanding with our broker, and payment terms — Pinnacle's standard is net-45, our quote says net-30."),
            ("meeting", "Terms session with Rosa and their controller: they pushed net-45; we floated net-30 with a 2% early-pay credit. Rosa is taking the credit option to the board with the retrofit vote."),
            ("email", "Re-sent the COI request to the broker and copied Rosa so Pinnacle can see it is in motion before the board date."),
            ("call", "Voicemail from Rosa: the board vote moved up a week. She asked whether we can hold the 14-week lead time if Pinnacle signs by Friday. Flagged to scheduling."),
        ),
    },
    {
        "key": "hill_country",
        "company": {"name": "Hill Country ISD", "segment": "education"},
        "contacts": (("Marisol Trevino", "Facilities Director"), ("Glenn Okafor", "CFO/Controller")),
        "deal": {"title": "Hill Country ISD service agreement renewal", "stage": "negotiation", "amount": 48000},
        "beats": (
            ("call", "Marisol Trevino called: the Hill Country ISD board is taking the district maintenance contract to competitive bid this cycle. Apex Air Systems has submitted."),
            ("note", "Account context: 6 years incumbent on Hill Country ISD — 11 campuses, 96 rooftop units (mostly Kestrel KX-90s). Current agreement: $48,000/yr, expires end of July."),
            ("email", "Sent Marisol the renewal proposal at $48,000/yr, same scope: quarterly PM on all campuses, 4-hour emergency response during school hours."),
            ("meeting", "Renewal QBR with Marisol and Glenn Okafor (CFO): uptime 99.2% across the KX-90 fleet, average emergency response 3.1 hours, zero school days lost to HVAC since 2024."),
            ("note", "Renewal is at risk on price alone. Proposed an 8% loyalty discount ($44,160/yr) to defuse the Apex bid — needs Greenlight before it goes to the district."),
            ("call", "Glenn Okafor wants an apples-to-apples scope comparison against the Apex bid before the board packet closes."),
            ("email", "Sent Glenn the scope-comparison matrix: Apex's bid excludes filter media and after-hours response — line-itemed the gap."),
            ("note", "Marisol, off the record: the facilities committee prefers continuity; the board just needs cover on price. The 8% letter likely closes it."),
            ("call", "Decision expected at the June board meeting. Marisol asked us to hold the discount offer open through the vote."),
        ),
    },
    {
        "key": "cedar_park",
        "company": {"name": "Cedar Park Surgical Center", "segment": "healthcare"},
        "contacts": (("Beatriz Lindqvist", "Director of Ops"), ("Hal Crowder", "Procurement Manager")),
        "deal": {"title": "Cedar Park Surgical Center OR air-handling install", "stage": "proposal", "amount": 132000},
        "beats": (
            ("meeting", "Site scoping at Cedar Park Surgical Center with Beatriz Lindqvist: OR air-handling install — two AHUs, HEPA terminal filtration, room-pressure monitoring across 4 ORs and sterile storage."),
            ("email", "Proposal sent: $132,000 — equipment, install, balancing, and commissioning, sequenced around the surgery schedule (nights and weekends)."),
            ("call", "Beatriz countered on scope: she wants the room-pressure monitoring package pulled to a later phase to get under this year's capital budget."),
            ("note", "Compliance subplot: Cedar Park's state health-facility licensure audit is this fall — they need IQ/OQ commissioning documentation from us as part of the package. Flagged to our controls sub."),
            ("email", "Sent option B: $118,000 with monitoring deferred, plus the compliance-documentation plan (IQ/OQ protocols, balancing reports, filter certs)."),
            ("note", "Procurement step: Hal Crowder requires the vendor packet — W-9, insurance certs, references. Submitted today."),
            ("call", "Beatriz is leaning back to the full $132K option A if we can phase the payments across two fiscal years. Checking with our controller."),
            ("meeting", "Walked the mechanical mezzanine with their engineer of record; confirmed structural capacity for the new AHUs. Decision targeted before their July capital meeting."),
        ),
    },
    {
        "key": "brazos",
        "company": {"name": "Brazos Logistics", "segment": "industrial"},
        "contacts": (("Dewayne Kessler", "Director of Ops"), ("Tamika Brandt", "Procurement Manager")),
        "deal": {"title": "Brazos Logistics quarterly PM contract", "stage": "proposal", "amount": 36000},
        "beats": (
            ("meeting", "Walked all three Brazos Logistics cross-dock facilities with Dewayne Kessler: 41 rooftop units across the sites, a third of them past 12 years old, no consistent PM history."),
            ("email", "Sent the quarterly PM proposal: $36,000/yr covering all 41 RTUs, filters and belts included, priority dispatch for dock-door heaters in winter."),
            ("call", "Dewayne is demanding a 12% discount and says Apex Air Systems quoted a cheaper per-unit rate. He runs ops lean and negotiates everything."),
            ("note", "Our discount floor is 10% without VP sign-off. A 12% quote has to clear Greenlight — do not issue without approval."),
            ("email", "Countered with 7% as a multi-site discount plus a no-charge filter program for year one; held the line on response times."),
            ("call", "Dewayne holding at 12%. Tamika Brandt (procurement) hinted they would sign this week at that number."),
            ("note", "Quote at 12% drafted for the approval queue with the Apex context attached; commercial call needed."),
        ),
    },
    {
        "key": "lantana",
        "company": {"name": "Lantana Hospitality Group", "segment": "hospitality"},
        "contacts": (("Sofia Marchetti", "Property Manager"), ("Quincy Abernathy", "Director of Ops")),
        "deal": {"title": "Lantana Hospitality walk-in cooler repair + gasket program", "stage": "qualified", "amount": 6800},
        "beats": (
            ("call", "Sofia Marchetti reported the walk-in cooler icing up at the Lantana Domain-area restaurant; line cooks are shutting the unit down overnight."),
            ("note", "Tech visit: replaced a failed evaporator-fan motor on the Permafrost PF-9 walk-in. Found worn door gaskets at this location and two others on the same route."),
            ("email", "Sent Sofia the repair summary; flagged the gasket wear at three locations and recommended replacing all of them in one visit."),
            ("note", "Upsell drafted: gasket replacements across the three locations at $850 all-in. Follow-up email queued for approval."),
            ("call", "Sofia asked for the gasket quote in writing before her Friday ops review — she expects a quick yes."),
        ),
    },
    {
        "key": "mueller",
        "company": {"name": "Mueller Commons REIT", "segment": "office_reit"},
        "contacts": (("Avery Stanhope", "Property Manager"), ("Bo Lindgren", "Chief Engineer")),
        "deal": {"title": "Mueller Commons service agreement win-back", "stage": "negotiation", "amount": 30000},
        "beats": (
            ("note", "Account review, Mueller Commons REIT: we missed the last 3 QBRs after our account manager left. The relationship is cold; renewal is in 60 days."),
            ("call", "Avery Stanhope was blunt: Apex Air Systems quoted 15% under our current rate, and Mueller's asset manager wants a reason not to switch."),
            ("meeting", "Win-back meeting on-site with Avery and chief engineer Bo Lindgren. Bo backs our techs — 41 closed tickets, no repeat failures on the ClimaCore CC-30 towers."),
            ("note", "Post-mortem: the misses were ours. Win-back plan: a $1,200 service credit, a named account manager, and a written quarterly QBR cadence."),
            ("email", "Sent Avery the renewal-at-current-rate summary while the credit offer clears internal approval."),
            ("call", "Avery: the asset manager will take continuity to committee if the credit and the QBR commitment arrive in writing this week."),
            ("note", "Win-back email with the $1,200 service credit drafted; queued for Greenlight."),
            ("meeting", "Bo walked us through the cooling-tower fill replacement he wants next quarter — scoped into the renewal as an option, not a condition."),
            ("call", "Committee meets Thursday. Avery asked for the final letter no later than Wednesday noon."),
        ),
    },
    {
        "key": "travis_heights",
        "company": {"name": "Travis Heights Medical Plaza", "segment": "healthcare"},
        "contacts": (("Noor Haddad", "Facilities Director"), ("Felix Arambula", "Chief Engineer")),
        "deal": {"title": "Travis Heights Medical Plaza renewal FY27", "stage": "closed_won", "amount": 52000},
        "beats": (
            ("call", "Renewal cycle opened with Noor Haddad at Travis Heights Medical Plaza: current agreement $52,000/yr, expires June 30."),
            ("meeting", "Annual review with Noor and Felix Arambula: 99.6% uptime across the plaza, both TruFlow TF-8 boilers passed inspection, negative-pressure suites held spec all year."),
            ("email", "Sent the renewal at $52,000/yr — same scope, added semi-annual coil cleaning at no charge as a loyalty inclusion."),
            ("note", "Noor flagged one concern: response time on the March after-hours call was 5 hours. Walked her through the on-call rotation fix we made in April."),
            ("call", "Noor confirmed the medical plaza's ownership group approved the renewal without taking it to bid."),
            ("email", "Renewal docs sent for signature via their counsel."),
            ("note", "Countersigned agreement received. Renewal closed at $52,000/yr through June 2027."),
            ("meeting", "Kickoff for the new term with Felix: quarterly schedule locked, filter program mapped to the suite-turnover calendar."),
            ("email", "Sent Noor the year-one service calendar and the escalation contact card."),
            ("call", "Noor agreed to act as a reference for healthcare prospects — happy to take one call a quarter."),
            ("note", "Logged Travis Heights as the reference customer for healthcare facilities; Noor Haddad approved being named."),
        ),
    },
    {
        "key": "coppell",
        "company": {"name": "Coppell Distribution Center", "segment": "industrial"},
        "contacts": (("Russ Vandermeer", "Procurement Manager"), ("Ines Calloway", "Director of Ops")),
        "deal": {"title": "Coppell Distribution Center RTU replacement, phase one", "stage": "closed_lost", "amount": 96000},
        "beats": (
            ("call", "Inbound from Russ Vandermeer, procurement at Coppell Distribution Center: RFP for a full RTU replacement program, 28 units on the main building."),
            ("meeting", "Depot walkthrough with Russ and Ines Calloway (ops): the units are 15-year-old builder-grade, three already on portable backup. They want a phased replacement over two quarters."),
            ("email", "Sent the phased replacement proposal: $96,000 for phase one (10 units), crane and controls included."),
            ("note", "Competitive heat: Apex Air Systems is bidding aggressively; Russ shares numbers freely to push price."),
            ("call", "Russ: Apex came in 'well under' our number. He asked for our best and final."),
            ("email", "Best-and-final sent: held scope, trimmed 4% with a value-engineering swap on the curbs. Held the line beyond that — below our floor the project loses money."),
            ("meeting", "Final pitch to Ines on lifecycle cost: our units carry a 10-year compressor warranty vs Apex's 5. She was sympathetic; procurement owns the call."),
            ("call", "Russ confirmed they are signing with Apex Air Systems at roughly 18% under our final number."),
            ("note", "Marked closed_lost on price. Post-mortem filed: we will not chase Apex below floor; flagged Coppell for a service-rescue campaign next winter."),
            ("email", "Sent Russ a no-hard-feelings close-out note; left the door open for emergency service coverage Apex can't staff."),
        ),
    },
)

# ---------------------------------------------------------------------------
# 6 PENDING approvals — designed against the L2 thresholds ($1,000 / 10%); brief's table.
# `arc` resolves to the hero deal's ref_id + primary-contact email at build time.
# ---------------------------------------------------------------------------
PENDING_APPROVALS = (
    {"arc": "lantana", "agent": "followup-agent", "value": 850,
     "action": {"action": "send_email",
                "subject": "Walk-in cooler gasket replacements — 3 locations",
                "body_preview": "Hi Sofia — quote attached for the door-gasket replacements at all three locations, $850 all-in, one visit."},
     "reasoning": "Sofia asked for the gasket quote in writing before her Friday ops review; tech found worn gaskets at three locations during the PF-9 repair. $850 is under the L2 auto-execute ceiling — at L2 this email would send itself."},
    {"arc": "hill_country", "agent": "pipeline-agent", "value": 48000, "discount": 0.08,
     "action": {"action": "send_email",
                "subject": "Hill Country ISD renewal — 8% loyalty discount",
                "body_preview": "Hi Marisol — attached is the renewal letter at $44,160/yr (an 8% loyalty discount) with the scope comparison the board asked for."},
     "reasoning": "Renewal at risk to the Apex bid on price alone; QBR data (99.2% uptime, 3.1-hr response) supports continuity. 8% is inside the discount floor, but $48,000 exceeds the value ceiling — queues at any level below L3."},
    {"arc": "cedar_park", "agent": "pipeline-agent", "value": 132000,
     "action": {"action": "update_deal", "field": "stage", "from": "proposal", "to": "negotiation"},
     "reasoning": "Beatriz countered on scope (monitoring deferred vs phased payments) and procurement has the vendor packet — the proposal is under active negotiation. Stage should reflect it for pipeline reporting."},
    {"arc": "brazos", "agent": "quote-agent", "value": 36000, "discount": 0.12,
     "action": {"action": "issue_quote", "amount": 31680, "discount": 0.12,
                "terms": "41 RTUs, 3 sites, quarterly PM, year-one filter program"},
     "reasoning": "Dewayne is holding at 12% and procurement signaled they would sign this week at that number. 12% trips the discount guard independently of value — cannot issue without a human call."},
    {"arc": "mueller", "agent": "followup-agent", "value": 1200,
     "action": {"action": "send_email",
                "subject": "Mueller Commons — our commitment for the next term",
                "body_preview": "Avery — attached is the renewal letter with a $1,200 service credit, your named account manager, and the written quarterly QBR cadence."},
     "reasoning": "Win-back letter must arrive before Thursday's committee. The $1,200 credit sits just over the $1,000 L2 ceiling — close to the line, but the line is real."},
    {"arc": "westlake", "agent": "pipeline-agent", "value": 284000,
     "action": {"action": "update_deal", "field": "stage", "from": "negotiation", "to": "closed_won"},
     "reasoning": "Rosa's voicemail says the board vote moved up and Pinnacle may sign by Friday. Premature until the COI lands and terms are countersigned — a human should edit or deny this one."},
)

# 8 DECIDED approvals (6 approved, 2 denied w/ messages) so the queue's history isn't empty.
DECIDED_APPROVALS = (
    {"arc": "westlake", "agent": "followup-agent", "value": 284000, "status": "approved",
     "action": {"action": "send_email", "subject": "Westlake Galleria retrofit — proposal v2",
                "body_preview": "Rosa — proposal v2 attached: $284,000 total, 14-week lead time, phased cutover plan."},
     "reasoning": "Walkthrough scope settled with Rosa and Emory; proposal v2 reflects the phased cutover they asked for."},
    {"arc": "hill_country", "agent": "pipeline-agent", "value": 48000, "status": "approved",
     "action": {"action": "update_deal", "field": "stage", "from": "qualified", "to": "negotiation"},
     "reasoning": "District went to competitive bid; renewal moved from routine to actively contested."},
    {"arc": "cedar_park", "agent": "quote-agent", "value": 132000, "status": "approved",
     "action": {"action": "issue_quote", "amount": 132000,
                "terms": "2 AHUs, HEPA terminal filtration, pressure monitoring, commissioning"},
     "reasoning": "Scoping meeting settled the OR air-handling package; Beatriz asked for the formal quote."},
    {"arc": "brazos", "agent": "followup-agent", "value": 36000, "status": "approved",
     "action": {"action": "send_email", "subject": "Brazos PM contract — multi-site discount + filter program",
                "body_preview": "Dewayne — countered at 7% multi-site with a no-charge year-one filter program; response times unchanged."},
     "reasoning": "Counter keeps us above the discount floor while answering the Apex per-unit comparison."},
    {"arc": "travis_heights", "agent": "followup-agent", "value": 52000, "status": "approved",
     "action": {"action": "send_email", "subject": "Travis Heights renewal — signature copies",
                "body_preview": "Noor — countersigned copies attached; kickoff invitation to follow."},
     "reasoning": "Ownership group approved without bid; send the executed copies and schedule kickoff."},
    {"arc": "travis_heights", "agent": "pipeline-agent", "value": 52000, "status": "approved",
     "action": {"action": "update_deal", "field": "stage", "from": "negotiation", "to": "closed_won"},
     "reasoning": "Countersigned agreement received; renewal closed at $52,000/yr through June 2027."},
    {"arc": "coppell", "agent": "quote-agent", "value": 96000, "status": "denied",
     "deny_message": "18% under floor is below cost. Max 10% without VP sign-off — do not match Apex.",
     "action": {"action": "issue_quote", "amount": 78720, "discount": 0.18,
                "terms": "phase one, 10 RTUs, price-match request"},
     "reasoning": "Russ asked us to match Apex's number; drafting the match for a commercial decision."},
    {"arc": "mueller", "agent": "followup-agent", "value": 1200, "status": "denied",
     "deny_message": "Wrong audience — the win-back letter goes to the property manager only, not all building tenants.",
     "action": {"action": "send_email", "subject": "Service update for Mueller Commons tenants",
                "body_preview": "A note to all Mueller Commons tenants about upcoming mechanical work..."},
     "reasoning": "Drafted a tenant-wide notice alongside the win-back; flagging for review of the audience."},
)

# ---------------------------------------------------------------------------
# 2 saved views — validate against shared/schemas/view_spec.schema.json; every member
# exists in the repo Cube model (semantic/model/cubes/*.js).
# ---------------------------------------------------------------------------
SAVED_VIEWS = (
    {
        "view_id": "pipeline-health",
        "title": "Pipeline health",
        "version": 1,
        "source_prompt": "Show me overall pipeline health",
        "semantic_refs": ["Deals.pipeline_value", "Deals.count", "Deals.stage"],
        "layout": [
            {"type": "kpi", "title": "Open pipeline ($)", "metric": "Deals.pipeline_value",
             "filter": {"filters": [{"member": "Deals.stage", "operator": "notEquals",
                                     "values": ["closed_won", "closed_lost"]}]}},
            {"type": "chart", "title": "Deals by stage", "encoding": "vega-lite",
             "query": {"measures": ["Deals.count"], "dimensions": ["Deals.stage"]}},
            {"type": "table", "title": "Top open deals",
             "query": {"measures": ["Deals.pipeline_value"],
                       "dimensions": ["Deals.title", "Deals.stage"],
                       "filters": [{"member": "Deals.stage", "operator": "notEquals",
                                    "values": ["closed_won", "closed_lost"]}]}},
        ],
    },
    {
        "view_id": "renewals-next-90d",
        "title": "Renewals — next 90 days",
        "version": 1,
        "source_prompt": "Which service agreements renew in the next 90 days?",
        "semantic_refs": ["Deals.count", "Deals.pipeline_value", "Deals.title"],
        "layout": [
            {"type": "kpi", "title": "Renewal deals in flight", "metric": "Deals.count",
             "filter": {"filters": [{"member": "Deals.title", "operator": "contains",
                                     "values": ["renewal"]}]}},
            {"type": "table", "title": "Open renewals",
             "query": {"measures": ["Deals.pipeline_value"],
                       "dimensions": ["Deals.title", "Deals.stage", "Deals.created_at"],
                       "filters": [{"member": "Deals.title", "operator": "contains",
                                    "values": ["renewal"]},
                                   {"member": "Deals.stage", "operator": "notEquals",
                                    "values": ["closed_won", "closed_lost"]}]}},
        ],
    },
)

# ---------------------------------------------------------------------------
# 8 longer authored documents (one per hero arc) — these make RAG answers rich.
# Loaded into `documents` with source='upload'; embedding happens at load time.
# ---------------------------------------------------------------------------
LONG_DOCS = (
    ("westlake", "Site-visit report — Westlake Galleria central plant (Pinnacle Property Partners)", """\
Site-visit report, Westlake Galleria central plant. Customer: Pinnacle Property Partners.
Attendees: Rosa Camarillo (Facilities Director), Emory Voss (Chief Engineer), our retrofit lead.

Findings. The plant runs two NorthCool C-450 centrifugal chillers, both installed 17 years ago
and both on R-22. Chiller #2 failed during the afternoon peak: compressor B shows a scored
crankshaft with metal found in the oil sample. The emergency repair (compressor isolation,
load shift to chiller #1) restored cooling the same day, but chiller #1 is now carrying the
full galleria load with no redundancy through the hottest part of the season. Eddy-current
testing on both evaporator bundles shows wall loss consistent with age. Refrigerant: R-22
availability and cost make any major repair on these units throwaway money.

Recommendation. Full retrofit of both units with high-efficiency magnetic-bearing chillers,
BAS integration, and a phased cutover (one unit at a time, weekend crane day for rigging)
so the galleria never loses cooling. Proposal v2 reflects this scope at $284,000 with a
14-week equipment lead time.

Open items. (1) COI naming Pinnacle Property Partners as additional insured — requested from
our broker, outstanding. (2) Payment terms: Pinnacle standard is net-45; our quote is net-30,
with a net-30 + 2% early-pay credit option on the table. (3) Board approval pending — the
property board vote was moved up a week; if Pinnacle signs by Friday we hold the lead-time
slot with the manufacturer."""),
    ("hill_country", "QBR notes — Hill Country ISD maintenance agreement", """\
Quarterly business review, Hill Country ISD. Attendees: Marisol Trevino (Facilities Director),
Glenn Okafor (CFO/Controller), our account team.

Fleet and performance. The district runs 96 rooftop units across 11 campuses, predominantly
Kestrel KX-90s. Trailing-twelve-month uptime: 99.2%. Average emergency response: 3.1 hours
against a 4-hour school-hours commitment. Zero school days lost to HVAC since 2024. Filter
media, belts, and condensate treatment are included in the current $48,000/yr scope.

Competitive situation. The board has taken the maintenance contract to competitive bid this
cycle; Apex Air Systems has submitted. Their bid excludes filter media and after-hours
response — the scope-comparison matrix sent to Glenn line-items the gap. The facilities
committee prefers continuity; the board needs cover on price.

Action. An 8% loyalty discount has been proposed internally (renewal at $44,160/yr) to
defuse the bid. The discount letter is queued for approval and must stay open through the
June board meeting per Marisol's request. Six years of incumbency and the uptime record are
the story; price is the only open vector."""),
    ("cedar_park", "Proposal summary — Cedar Park Surgical Center OR air-handling install", """\
Proposal summary, Cedar Park Surgical Center operating-room air-handling installation.
Customer contacts: Beatriz Lindqvist (Director of Ops), Hal Crowder (Procurement Manager).

Scope, option A ($132,000): two air-handling units serving 4 ORs and sterile storage, HEPA
terminal filtration, room-pressure monitoring, full test-and-balance, and commissioning.
All disruptive work sequenced nights and weekends around the surgery schedule. Scope,
option B ($118,000): identical except room-pressure monitoring deferred to a later phase —
offered after Beatriz's counter to fit this year's capital budget. Beatriz has since signaled
she would take full option A if payments phase across two fiscal years; controller review of
phased terms is in progress.

Compliance. The center's state health-facility licensure audit lands this fall. The package
includes the documentation set their auditor will ask for: IQ/OQ commissioning protocols,
air-balance reports, and filter certifications. This documentation requirement is a
differentiator — the competing bid does not include it.

Procurement status: vendor packet (W-9, insurance certificates, references) submitted to
Hal Crowder. Structural review of the mechanical mezzanine is complete; the engineer of
record confirmed capacity for both AHUs. Decision targeted before the July capital meeting."""),
    ("brazos", "Scope of work — Brazos Logistics preventive-maintenance program", """\
Scope-of-work summary, Brazos Logistics quarterly preventive-maintenance program.
Customer contacts: Dewayne Kessler (Director of Ops), Tamika Brandt (Procurement Manager).

Sites and equipment. Three cross-dock facilities; 41 rooftop units total, roughly a third
older than 12 years, with no consistent PM history. Winter dock-door heater reliability is
the operational pain point — priority dispatch for heater failures is written into the scope.

Program ($36,000/yr): quarterly PM on all 41 RTUs including filters and belts, condensate
and coil service on the summer visits, heater checks on the fall visit, and a documented
deficiency list after every round so capital planning has real data.

Commercial status. Dewayne is demanding a 12% discount, citing a cheaper per-unit rate from
Apex Air Systems. Our counter: 7% multi-site discount plus a no-charge year-one filter
program, response commitments unchanged. The 12% number exceeds the 10% floor that requires
VP sign-off, so the 12% quote sits in the approval queue as a commercial decision, not a
field one. Procurement signaled they would sign this week at 12%."""),
    ("lantana", "Repair recommendation — Lantana Hospitality walk-in refrigeration", """\
Repair recommendation memo, Lantana Hospitality Group walk-in refrigeration.
Customer contact: Sofia Marchetti (Property Manager).

Incident. The Domain-area restaurant's Permafrost PF-9 walk-in cooler was icing the
evaporator and losing temperature overnight; kitchen staff were shutting the unit down to
defrost manually. Root cause: failed evaporator-fan motor. Replaced same-visit; the unit
held 36F overnight after the repair.

Route findings. Door gaskets at this location are torn at the hinge corners, and the same
wear pattern shows at two other Lantana locations on the same service route. Worn gaskets
drive exactly this failure mode: moist air infiltration, icing, fan-motor strain, and food
safety risk during peak service.

Recommendation: replace door gaskets at all three locations in a single visit — $850 all-in,
parts and labor. Sofia wants the quote in writing before her Friday ops review. This is a
small ticket with outsized goodwill value on a 14-location account."""),
    ("mueller", "Account review — Mueller Commons REIT win-back plan", """\
Account review, Mueller Commons REIT. Customer contacts: Avery Stanhope (Property Manager),
Bo Lindgren (Chief Engineer).

Where we stand. The renewal is in 60 days and the relationship is cold — we missed the last
three QBRs after our account manager departed, and nobody picked up the cadence. Apex Air
Systems has quoted 15% under our current rate, and Mueller's asset manager wants a reason
not to switch. Bo Lindgren remains our advocate on the engineering side: 41 closed tickets,
no repeat failures on the ClimaCore CC-30 cooling towers, and he trusts our techs by name.

The win-back plan: (1) a $1,200 service credit on the next term, (2) a named account manager
with contact card, (3) a written quarterly QBR cadence with dates on the calendar before
signature, and (4) the cooling-tower fill replacement Bo wants next quarter scoped into the
renewal as an option, not a condition. The misses were ours; the letter says so plainly.

Timing. The committee meets Thursday; Avery needs the final letter in writing by Wednesday
noon. The credit requires approval — it sits just over the auto-execute threshold."""),
    ("travis_heights", "Kickoff notes — Travis Heights Medical Plaza FY27 term", """\
Kickoff meeting notes, Travis Heights Medical Plaza, FY27 service term.
Customer contacts: Noor Haddad (Facilities Director), Felix Arambula (Chief Engineer).

Renewal summary. Closed at $52,000/yr through June 2027, approved by the ownership group
without competitive bid. Loyalty inclusion: semi-annual coil cleaning at no charge. The one
concern raised in the cycle — a 5-hour after-hours response in March — was addressed by the
April on-call rotation fix and a written escalation path; Noor accepted the remedy.

Performance baseline carried into the new term: 99.6% uptime plaza-wide, both TruFlow TF-8
boilers passed inspection, negative-pressure suites held specification all year.

Term-one plan agreed with Felix: quarterly PM schedule locked to the suite-turnover
calendar, filter program mapped per suite, year-one service calendar and escalation contact
card delivered to Noor.

Reference status: Noor Haddad agreed to act as a named reference for healthcare prospects,
capped at one call per quarter. Travis Heights is the reference customer for healthcare
facilities."""),
    ("coppell", "Loss post-mortem — Coppell Distribution Center RTU program", """\
Loss post-mortem, Coppell Distribution Center RTU replacement program (closed_lost).
Customer contacts: Russ Vandermeer (Procurement Manager), Ines Calloway (Director of Ops).

What happened. Coppell ran an RFP for a phased replacement of 28 rooftop units. Our phase-one
proposal: $96,000 for 10 units including crane and controls. Apex Air Systems bid
aggressively throughout; Russ shared numbers freely to drive price. Our best-and-final held
scope and trimmed 4% via a value-engineering swap on the curbs. Coppell signed with Apex at
roughly 18% under our final number.

Why we lost: price, full stop. Ines was sympathetic to the lifecycle-cost argument — our
units carry a 10-year compressor warranty against Apex's 5 — but procurement owned the
decision and bought the low number.

What we keep: discipline. Below our floor the project loses money; we do not chase Apex
there. A price-match quote at 18% off was drafted and denied internally for exactly that
reason. Follow-up: Coppell is flagged for a service-rescue campaign next winter — Apex
historically understaffs emergency response in this corridor, and the door was left open
on friendly terms."""),
)


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------
def _uid(seed: int, label: str) -> str:
    return str(uuid.uuid5(DEMO_NAMESPACE, f"seed{seed}:{label}"))


def _slug(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalnum())


def _email(full_name: str, domain: str) -> str:
    parts = [_slug(p) for p in full_name.split()]
    return f"{parts[0]}.{parts[-1]}@{domain}"


def _phone(i: int) -> str:
    return f"+1-{AREA_CODES[i // 100]}-555-01{i % 100:02d}"


def _weighted(rng: random.Random, pairs) -> str:
    r, acc = rng.random(), 0.0
    for value, w in pairs:
        acc += w
        if r < acc:
            return value
    return pairs[-1][0]


def _biz_time(rng: random.Random, dt: datetime) -> datetime:
    """Snap a datetime to US-Central business hours."""
    return dt.replace(hour=rng.randint(8, 17), minute=rng.choice((0, 5, 10, 15, 20, 30, 40, 45, 50)),
                      second=0, microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def generate(seed: int = DEFAULT_SEED, tenant_id: str = DEFAULT_TENANT_ID,
             anchor_date: str = DEFAULT_ANCHOR_DATE) -> dict:
    """Build the full dataset as a plain dict. Pure function of (seed, tenant_id, anchor)."""
    rng = random.Random(seed)
    anchor = datetime.fromisoformat(anchor_date).replace(hour=12, tzinfo=CENTRAL)

    companies: list[dict] = []
    contacts: list[dict] = []
    deals: list[dict] = []
    activities: list[dict] = []
    approvals: list[dict] = []
    documents: list[dict] = []

    used_names = set()
    phone_i = 0
    arc_info: dict[str, dict] = {}  # key -> {company, primary_contact, deal}

    def add_company(name: str, n: int) -> dict:
        row = {
            "id": _uid(seed, f"company:{n}"),
            "name": name,
            "domain": f"{_slug(name)}.example",
            "ref_id": f"demo:company:{n}",
            "created_at": _iso(_biz_time(rng, anchor - timedelta(days=rng.uniform(180, 900)))),
        }
        companies.append(row)
        return row

    def add_contact(company: dict, full_name: str, n: int) -> dict:
        nonlocal phone_i
        row = {
            "id": _uid(seed, f"contact:{n}"),
            "company_id": company["id"],
            "name": full_name,
            "email": _email(full_name, company["domain"]),
            "phone": _phone(phone_i),
            "ref_id": f"demo:contact:{n}",
            "created_at": company["created_at"],
        }
        phone_i += 1
        used_names.add(full_name)
        contacts.append(row)
        return row

    def gen_name() -> str:
        while True:
            name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            if name not in used_names:
                return name

    # --- hero companies / contacts / deals (deterministic order: arcs first) ----------------
    company_n = 0
    contact_n = 0
    for arc in HERO_ARCS:
        for c in arc["contacts"]:
            used_names.add(c[0])  # reserve hero names before any tail sampling
    for arc in HERO_ARCS:
        comp = add_company(arc["company"]["name"], company_n)
        company_n += 1
        arc_contacts = []
        for full_name, _role in arc["contacts"]:
            arc_contacts.append(add_contact(comp, full_name, contact_n))
            contact_n += 1
        while len(arc_contacts) < 5:  # hero companies get 4-6 contacts; pad to 5
            arc_contacts.append(add_contact(comp, gen_name(), contact_n))
            contact_n += 1
        arc_info[arc["key"]] = {"company": comp, "contacts": arc_contacts,
                                "segment": arc["company"]["segment"]}

    # --- tail companies + contacts -----------------------------------------------------------
    tail_segments = [seg for seg, cfg in SEGMENTS.items() for _ in range(cfg["tail"])]
    prefixes = rng.sample(TAIL_NAME_PREFIXES, len(tail_segments))
    tail_contact_counts = [1] * 4 + [2] * 12 + [3] * 12 + [4] * 4  # sums to 80 -> 120 total
    rng.shuffle(tail_contact_counts)
    tail_companies: list[tuple[dict, str]] = []
    for i, seg in enumerate(tail_segments):
        name = f"{prefixes[i]} {rng.choice(SEGMENTS[seg]['suffixes'])}"
        comp = add_company(name, company_n)
        company_n += 1
        for _ in range(tail_contact_counts[i]):
            add_contact(comp, gen_name(), contact_n)
            contact_n += 1
        tail_companies.append((comp, seg))

    contacts_by_company: dict[str, list[dict]] = {}
    for c in contacts:
        contacts_by_company.setdefault(c["company_id"], []).append(c)

    # --- deals: heroes first, then the tail fill ---------------------------------------------
    deal_n = 0

    def add_deal(company: dict, contact: dict, title: str, stage: str, amount: int) -> dict:
        nonlocal deal_n
        lo, hi = STAGE_AGE_DAYS[stage]
        created = _biz_time(rng, anchor - timedelta(days=rng.uniform(lo, hi)))
        row = {
            "id": _uid(seed, f"deal:{deal_n}"),
            "company_id": company["id"],
            "contact_id": contact["id"],
            "title": title,
            "stage": stage,
            "amount": amount,
            "currency": "USD",
            "ref_id": f"demo:deal:{deal_n}",
            "created_at": _iso(created),
        }
        deal_n += 1
        deals.append(row)
        return row

    for arc in HERO_ARCS:
        info = arc_info[arc["key"]]
        d = arc["deal"]
        info["deal"] = add_deal(info["company"], info["contacts"][0],
                                d["title"], d["stage"], d["amount"])

    tail_stage_list = [s for s, n in TAIL_STAGE_COUNTS.items() for _ in range(n)]
    rng.shuffle(tail_stage_list)
    tail_deals: list[tuple[dict, str, dict]] = []  # (deal, segment, company)
    for i, stage in enumerate(tail_stage_list):
        comp, seg = tail_companies[i % len(tail_companies)]
        kind = _weighted(rng, TAIL_DEAL_KINDS)
        lo, hi, step = TAIL_AMOUNT_BANDS[kind]
        amount = int(round(rng.uniform(lo, hi) / step) * step)
        contact = rng.choice(contacts_by_company[comp["id"]])
        equipment, site = rng.choice(EQUIPMENT), rng.choice(SITES)
        title = rng.choice(TAIL_DEAL_TITLES[kind]).format(
            company=comp["name"], equipment=equipment, site=site)
        deal = add_deal(comp, contact, title, stage, amount)
        tail_deals.append((deal, seg, comp))

    contacts_by_id = {c["id"]: c for c in contacts}

    # --- activities ---------------------------------------------------------------------------
    act_n = 0

    def deal_timeline(deal: dict, n: int) -> list[datetime]:
        created = datetime.fromisoformat(deal["created_at"])
        if deal["stage"] in OPEN_STAGES:
            end = anchor - timedelta(days=rng.uniform(0.5, 3))
        else:
            end = created + (anchor - created) * rng.uniform(0.5, 0.85)
        span = end - created
        times = [_biz_time(rng, created + span * ((i + rng.uniform(0.2, 0.8)) / n))
                 for i in range(n)]
        return sorted(times)

    def add_activity(deal: dict, contact: dict, kind: str, body: str, when: datetime) -> dict:
        nonlocal act_n
        row = {
            "id": _uid(seed, f"activity:{act_n}"),
            "contact_id": contact["id"],
            "deal_id": deal["id"],
            "kind": kind,
            "body": body,
            "occurred_at": _iso(when),
        }
        act_n += 1
        activities.append(row)
        documents.append({
            "source": ACTIVITY_DOC_SOURCE[kind],
            "ref_id": f"demo:doc:act:{len(documents)}",
            "content": f"{kind.title()} — {deal['title']} ({when.date().isoformat()}): {body}",
            "created_at": _iso(when),
        })
        return row

    for arc in HERO_ARCS:  # hero beats: authored, in narrative order on a sorted timeline
        info = arc_info[arc["key"]]
        deal = info["deal"]
        times = deal_timeline(deal, len(arc["beats"]))
        for (kind, body), when in zip(arc["beats"], times):
            add_activity(deal, info["contacts"][0], kind, body, when)

    for deal, seg, comp in tail_deals:  # tail: templated narratives
        lo, hi = ACTIVITY_BAND[deal["stage"]]
        n = rng.randint(lo, hi)
        times = deal_timeline(deal, n)
        contact = contacts_by_id[deal["contact_id"]]
        equipment, site = rng.choice(EQUIPMENT), rng.choice(SITES)
        for when in times:
            kind = _weighted(rng, KIND_WEIGHTS)
            body = rng.choice(KIND_TEMPLATES[kind]).format(
                contact=contact["name"], first=contact["name"].split()[0],
                company=comp["name"], equipment=equipment, site=site,
                segment_noun=SEGMENTS[seg]["noun"])
            add_activity(deal, contact, kind, body, when)

    # --- approvals ----------------------------------------------------------------------------
    appr_n = 0

    def build_approval(spec: dict, status: str) -> dict:
        nonlocal appr_n
        info = arc_info[spec["arc"]]
        deal = info["deal"]
        action = dict(spec["action"])
        action["deal_ref"] = deal["ref_id"]
        action["deal"] = deal["title"]
        if action["action"] == "send_email":
            action["to"] = info["contacts"][0]["email"]
        if status == "pending":
            created = anchor - timedelta(days=rng.uniform(0.2, 3))
            decided_by = deny_message = decided_at = None
        else:
            created = anchor - timedelta(days=rng.uniform(5, 45))
            decided_at = _iso(created + timedelta(hours=rng.uniform(2, 30)))
            decided_by = "demo-admin"
            deny_message = spec.get("deny_message")
        row = {
            "id": _uid(seed, f"approval:{appr_n}"),
            "proposed_action": action,
            "agent": spec["agent"],
            "reasoning": spec["reasoning"],
            "value_at_stake": spec["value"],
            "status": status,
            "decided_by": decided_by,
            "deny_message": deny_message,
            "created_at": _iso(created),
            "decided_at": decided_at,
        }
        appr_n += 1
        approvals.append(row)
        return row

    for spec in DECIDED_APPROVALS:
        build_approval(spec, spec["status"])
    for spec in PENDING_APPROVALS:
        build_approval(spec, "pending")

    # --- saved views ----------------------------------------------------------------------------
    saved_views = []
    for i, spec in enumerate(SAVED_VIEWS):
        saved_views.append({
            "id": _uid(seed, f"saved_view:{i}"),
            "view_id": spec["view_id"],
            "version": spec["version"],
            "spec_json": spec,
            "semantic_refs": spec["semantic_refs"],
            "source_prompt": spec["source_prompt"],
            "created_by": "demo-seed",
            "created_at": _iso(anchor - timedelta(days=rng.uniform(1, 14))),
        })

    # --- long authored docs -----------------------------------------------------------------
    for i, (_arc, title, body) in enumerate(LONG_DOCS):
        documents.append({
            "source": "upload",
            "ref_id": f"demo:doc:long:{i}",
            "content": f"{title}\n\n{body}",
            "created_at": _iso(_biz_time(rng, anchor - timedelta(days=rng.uniform(2, 60)))),
        })

    return {
        "meta": {
            "generator": GENERATOR,
            "version": GENERATOR_VERSION,
            "seed": seed,
            "anchor_date": anchor_date,
            "tenant_id": tenant_id,
            "decision_brief": DECISION_BRIEF,
            "ratification": RATIFICATION,
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


# ---------------------------------------------------------------------------
# output formats
# ---------------------------------------------------------------------------
def to_json(dataset: dict) -> str:
    return json.dumps(dataset, indent=2, sort_keys=True) + "\n"


def _q(value) -> str:
    """SQL-literal quoting for the generated (authored/templated-only) strings."""
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        return "'" + json.dumps(value, sort_keys=True).replace("'", "''") + "'::jsonb"
    return "'" + str(value).replace("'", "''") + "'"


def _insert(table: str, cols: list[str], rows: list[dict], tenant_id: str,
            chunk: int = 50) -> list[str]:
    stmts = []
    for start in range(0, len(rows), chunk):
        values = []
        for row in rows[start:start + chunk]:
            vals = [_q(tenant_id)] + [_q(row.get(c)) for c in cols]
            values.append("    (" + ", ".join(vals) + ")")
        stmts.append(
            f"INSERT INTO {table} (tenant_id, {', '.join(cols)}) VALUES\n"
            + ",\n".join(values) + ";"
        )
    return stmts


def to_sql(dataset: dict) -> str:
    """One idempotent transaction: wipe this tenant's demo rows, then re-insert.

    Meant to run as the RLS-bound crm_app role (the seed_demo_tenant.py pattern): every
    DELETE/INSERT is scoped by the tenant_isolation policy via SET LOCAL app.current_tenant.
    documents.embedding stays NULL — backfill via the ingest embedder seam before RAG use.
    """
    meta = dataset["meta"]
    tenant = meta["tenant_id"]
    out = [
        f"-- Generated by {GENERATOR} v{GENERATOR_VERSION} — seed {meta['seed']}, "
        f"anchor {meta['anchor_date']}. DO NOT EDIT BY HAND.",
        f"-- Spec: {DECISION_BRIEF} (ratification {meta['ratification']})",
        "-- Run INSIDE the VPC as crm_app (one-off ECS task / psql -v ON_ERROR_STOP=1).",
        "-- Idempotent: wipes this tenant's rows (RLS-scoped), then re-inserts.",
        "-- documents.embedding is left NULL — run the embed pass (Titan V2 via the ingest",
        "-- embedder seam) before expecting rag.search to retrieve this corpus.",
        "BEGIN;",
        f"SET LOCAL app.current_tenant = {_q(tenant)};",
        "DELETE FROM activities;",
        "DELETE FROM approvals;",
        "DELETE FROM deals;",
        "DELETE FROM contacts;",
        "DELETE FROM companies;",
        "DELETE FROM saved_views;",
        "DELETE FROM documents WHERE ref_id LIKE 'demo:%';",
    ]
    out += _insert("companies", ["id", "name", "domain", "ref_id", "created_at"],
                   dataset["companies"], tenant)
    out += _insert("contacts", ["id", "company_id", "name", "email", "phone", "ref_id",
                                "created_at"], dataset["contacts"], tenant)
    out += _insert("deals", ["id", "company_id", "contact_id", "title", "stage", "amount",
                             "currency", "ref_id", "created_at"], dataset["deals"], tenant)
    out += _insert("activities", ["id", "contact_id", "deal_id", "kind", "body", "occurred_at"],
                   dataset["activities"], tenant)
    out += _insert("approvals", ["id", "proposed_action", "agent", "reasoning", "value_at_stake",
                                 "status", "decided_by", "deny_message", "created_at",
                                 "decided_at"], dataset["approvals"], tenant)
    out += _insert("saved_views", ["id", "view_id", "version", "spec_json", "semantic_refs",
                                   "source_prompt", "created_by", "created_at"],
                   dataset["saved_views"], tenant)
    out += _insert("documents", ["source", "ref_id", "content", "created_at"],
                   dataset["documents"], tenant)
    out.append("COMMIT;")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the synthetic Meridian Mechanical demo-tenant dataset "
                    "(NO database connection — file/stdout output only).",
        epilog=f"Spec: {DECISION_BRIEF} — ratification {RATIFICATION}.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help=f"deterministic RNG seed (default {DEFAULT_SEED})")
    parser.add_argument("--format", choices=("json", "sql"), default="json",
                        help="output format (default json)")
    parser.add_argument("--tenant", default=DEFAULT_TENANT_ID,
                        help="tenant uuid for SQL scoping / meta (default: fixed demo uuid)")
    parser.add_argument("--anchor-date", default=DEFAULT_ANCHOR_DATE,
                        help="YYYY-MM-DD anchor that all timestamps backdate from "
                             f"(fixed default {DEFAULT_ANCHOR_DATE} keeps output byte-identical)")
    parser.add_argument("--out", default="-",
                        help="output path, or '-' for stdout (default stdout)")
    args = parser.parse_args(argv)

    dataset = generate(seed=args.seed, tenant_id=args.tenant, anchor_date=args.anchor_date)
    text = to_json(dataset) if args.format == "json" else to_sql(dataset)
    if args.out == "-":
        sys.stdout.write(text)
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        counts = dataset["meta"]["counts"]
        sys.stderr.write(f"wrote {args.out} ({args.format}, seed {args.seed}): {counts}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
