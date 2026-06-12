"""Seed one tenant with realistic CRM rows — run INSIDE the VPC as a one-off ECS task.

Connects as crm_app (the RLS-bound runtime role, via CRM_APP_SECRET_ARN + DB_HOST from the api task
definition) and inserts under `SET app.current_tenant`, so the seed itself exercises the isolation
path. Idempotent: wipes and re-seeds only this tenant's rows (RLS scopes the deletes).

Run (container override on the uplift-api task def):
  command: ["python","-c","<this file's content>"]  with env TENANT_ID=<uuid>

IMPORT-SAFE: importing this module touches no env/boto3/DB — the side effects live in main()
(tests import build_demo_approvals to pin the seeded approval shapes).
"""
from __future__ import annotations

COMPANIES = [
    ("Birchwood Capital", "birchwoodcap.com"),
    ("Halcyon Logistics", "halcyonlogistics.io"),
    ("Mesa Verde Health", "mesaverdehealth.com"),
    ("Northbeam Industrial", "northbeam.us"),
]

CONTACTS = [  # (company index, name, email, phone)
    (0, "Dana Whitfield", "dana@birchwoodcap.com", "+1-512-555-0117"),
    (0, "Marcus Oyelaran", "marcus@birchwoodcap.com", "+1-512-555-0142"),
    (1, "Priya Raghunathan", "priya@halcyonlogistics.io", "+1-737-555-0199"),
    (2, "Tom Cervantes", "tom@mesaverdehealth.com", "+1-210-555-0163"),
    (2, "Allie Schreiber", "allie@mesaverdehealth.com", "+1-210-555-0188"),
    (3, "Roy Nakamura", "roy@northbeam.us", "+1-915-555-0174"),
]

DEALS = [  # (company index, contact index, title, stage, amount)
    (0, 0, "Birchwood platform expansion", "negotiation", 84000),
    (0, 1, "Birchwood analytics add-on", "proposal", 18500),
    (1, 2, "Halcyon fleet rollout", "qualified", 132000),
    (2, 3, "Mesa Verde pilot", "new", 9500),
    (2, 4, "Mesa Verde clinic suite", "proposal", 47000),
    (3, 5, "Northbeam renewal FY27", "closed_won", 61000),
]

ACTIVITIES = [  # (contact index, deal index, kind, body)
    (0, 0, "call", "Walked Dana through the security review; she wants RLS docs."),
    (0, 0, "email", "Sent the revised order form (net-45 -> net-30)."),
    (2, 2, "meeting", "Fleet rollout scoping with Priya's ops leads; 3 depots phase 1."),
    (3, 3, "note", "Tom confirmed pilot budget approved by clinical board."),
    (5, 5, "email", "Renewal countersigned; kickoff scheduled."),
]


def build_demo_approvals(dids_by_title: dict[str, str]) -> list[tuple[dict, str, str, float]]:
    """The seeded pending Greenlight drafts: (proposed_action, agent, reasoning, value_at_stake).

    APPLIER-SHAPED, not display-shaped (the 2026-06-12 live find — the original seeds carried
    `deal`/`field`/`body_preview` and were un-applyable or un-approvable):
      * update_deal needs `deal_id` (a REAL seeded deal uuid) + `changes` — exactly what
        apply_update_deal reads; extra display keys are fine, missing applier keys are not.
      * send_email needs the FULL `body` WITH an unsubscribe mechanism, or the decide-time
        CAN-SPAM check rejects every approval (and the edit guard correctly refuses adding a
        novel `body` key later). The applier stays record_only until provider go-live.
      * issue_quote mirrors the IssueQuote tool shape (`deal_id` + `amount`); record_only too.
    Pinned by tests/unit/test_seed_approval_shapes.py against the REAL compliance choke point
    and appliers.
    """
    return [
        ({"action": "send_email",
          "to": "dana@birchwoodcap.com",
          "subject": "Updated order form + RLS security docs",
          "body": ("Hi Dana,\n\nAttached are the revised order form (net-30, as discussed) and "
                   "the row-level security overview you asked for on the call. Happy to walk "
                   "your security team through it this week if useful.\n\nBest,\nUplift for "
                   "Birchwood Capital\n\nIf you'd rather not receive these updates, reply "
                   "\"unsubscribe\" and we'll stop right away.")},
         "pipeline-agent",
         "Dana asked for security docs on the call; deal is in negotiation at $84k.", 84000),
        ({"action": "update_deal",
          "deal_id": dids_by_title["Mesa Verde clinic suite"],
          "changes": {"stage": "negotiation"},
          "deal": "Mesa Verde clinic suite"},  # display context only; the applier reads deal_id/changes
         "pipeline-agent",
         "Allie countered on seat count; proposal is now under active negotiation.", 47000),
        ({"action": "issue_quote",
          "deal_id": dids_by_title["Halcyon fleet rollout"],
          "amount": 132000,
          "company": "Halcyon Logistics",  # display context only
          "terms": "3-depot phase 1, annual"},
         "quote-agent",
         "Scoping meeting settled phase-1 size; quote requested by Friday.", 132000),
    ]


def main() -> None:
    import json
    import os

    import boto3
    import psycopg2
    from psycopg2.extras import Json

    tenant = os.environ["TENANT_ID"]

    sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    crm = json.loads(sm.get_secret_value(SecretId=os.environ["CRM_APP_SECRET_ARN"])["SecretString"])
    conn = psycopg2.connect(host=os.environ["DB_HOST"], port=5432,
                            dbname=os.environ.get("DB_NAME", "uplift"),
                            user=crm["username"], password=crm["password"])
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute("SET app.current_tenant = %s", (tenant,))

    # NOTE: approvals are deliberately NOT wiped — the audit-trail hardening REVOKEd DELETE on
    # approvals from crm_app (db/roles.sql), so the old wipe would now crash the seed; decided
    # rows are audit history anyway. Stale PENDING seeds should be denied through the API.
    for t in ("activities", "deals", "contacts", "companies", "saved_views"):
        cur.execute(f"DELETE FROM {t}")  # RLS scopes this to the tenant

    cids = []
    for name, domain in COMPANIES:
        cur.execute(
            "INSERT INTO companies (tenant_id, name, domain) VALUES (%s,%s,%s) RETURNING id",
            (tenant, name, domain))
        cids.append(cur.fetchone()[0])

    pids = []
    for ci, name, email, phone in CONTACTS:
        cur.execute(
            "INSERT INTO contacts (tenant_id, company_id, name, email, phone) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (tenant, cids[ci], name, email, phone))
        pids.append(cur.fetchone()[0])

    dids = []
    for ci, pi, title, stage, amount in DEALS:
        cur.execute(
            "INSERT INTO deals (tenant_id, company_id, contact_id, title, stage, amount) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (tenant, cids[ci], pids[pi], title, stage, amount))
        dids.append(cur.fetchone()[0])

    for pi, di, kind, body in ACTIVITIES:
        cur.execute(
            "INSERT INTO activities (tenant_id, contact_id, deal_id, kind, body) "
            "VALUES (%s,%s,%s,%s,%s)", (tenant, pids[pi], dids[di], kind, body))

    dids_by_title = {title: str(did) for (_, _, title, _, _), did in zip(DEALS, dids)}
    for action, agent, reasoning, value in build_demo_approvals(dids_by_title):
        cur.execute(
            "INSERT INTO approvals (tenant_id, proposed_action, agent, reasoning, value_at_stake, status) "
            "VALUES (%s,%s,%s,%s,%s,'pending')", (tenant, Json(action), agent, reasoning, value))

    cur.execute("SELECT (SELECT count(*) FROM companies), (SELECT count(*) FROM contacts), "
                "(SELECT count(*) FROM deals), (SELECT count(*) FROM activities), "
                "(SELECT count(*) FROM approvals)")
    print("seeded (companies, contacts, deals, activities, approvals):", cur.fetchone())
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
