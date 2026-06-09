"""Seed one tenant with realistic CRM rows — run INSIDE the VPC as a one-off ECS task.

Connects as crm_app (the RLS-bound runtime role, via CRM_APP_SECRET_ARN + DB_HOST from the api task
definition) and inserts under `SET app.current_tenant`, so the seed itself exercises the isolation
path. Idempotent: wipes and re-seeds only this tenant's rows (RLS scopes the deletes).

Run (container override on the uplift-api task def):
  command: ["python","-c","<this file's content>"]  with env TENANT_ID=<uuid>
"""
from __future__ import annotations

import json
import os

import boto3
import psycopg2
from psycopg2.extras import Json

TENANT = os.environ["TENANT_ID"]

sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
crm = json.loads(sm.get_secret_value(SecretId=os.environ["CRM_APP_SECRET_ARN"])["SecretString"])
conn = psycopg2.connect(host=os.environ["DB_HOST"], port=5432,
                        dbname=os.environ.get("DB_NAME", "uplift"),
                        user=crm["username"], password=crm["password"])
conn.autocommit = False
cur = conn.cursor()
cur.execute("SET app.current_tenant = %s", (TENANT,))

for t in ("activities", "deals", "contacts", "companies", "approvals", "saved_views"):
    cur.execute(f"DELETE FROM {t}")  # RLS scopes this to TENANT

companies = [
    ("Birchwood Capital", "birchwoodcap.com"),
    ("Halcyon Logistics", "halcyonlogistics.io"),
    ("Mesa Verde Health", "mesaverdehealth.com"),
    ("Northbeam Industrial", "northbeam.us"),
]
cids = []
for name, domain in companies:
    cur.execute(
        "INSERT INTO companies (tenant_id, name, domain) VALUES (%s,%s,%s) RETURNING id",
        (TENANT, name, domain))
    cids.append(cur.fetchone()[0])

contacts = [
    (cids[0], "Dana Whitfield", "dana@birchwoodcap.com", "+1-512-555-0117"),
    (cids[0], "Marcus Oyelaran", "marcus@birchwoodcap.com", "+1-512-555-0142"),
    (cids[1], "Priya Raghunathan", "priya@halcyonlogistics.io", "+1-737-555-0199"),
    (cids[2], "Tom Cervantes", "tom@mesaverdehealth.com", "+1-210-555-0163"),
    (cids[2], "Allie Schreiber", "allie@mesaverdehealth.com", "+1-210-555-0188"),
    (cids[3], "Roy Nakamura", "roy@northbeam.us", "+1-915-555-0174"),
]
pids = []
for cid, name, email, phone in contacts:
    cur.execute(
        "INSERT INTO contacts (tenant_id, company_id, name, email, phone) "
        "VALUES (%s,%s,%s,%s,%s) RETURNING id",
        (TENANT, cid, name, email, phone))
    pids.append(cur.fetchone()[0])

deals = [
    (cids[0], pids[0], "Birchwood platform expansion", "negotiation", 84000),
    (cids[0], pids[1], "Birchwood analytics add-on", "proposal", 18500),
    (cids[1], pids[2], "Halcyon fleet rollout", "qualified", 132000),
    (cids[2], pids[3], "Mesa Verde pilot", "new", 9500),
    (cids[2], pids[4], "Mesa Verde clinic suite", "proposal", 47000),
    (cids[3], pids[5], "Northbeam renewal FY27", "closed_won", 61000),
]
dids = []
for cid, pid, title, stage, amount in deals:
    cur.execute(
        "INSERT INTO deals (tenant_id, company_id, contact_id, title, stage, amount) "
        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (TENANT, cid, pid, title, stage, amount))
    dids.append(cur.fetchone()[0])

activities = [
    (pids[0], dids[0], "call", "Walked Dana through the security review; she wants RLS docs."),
    (pids[0], dids[0], "email", "Sent the revised order form (net-45 -> net-30)."),
    (pids[2], dids[2], "meeting", "Fleet rollout scoping with Priya's ops leads; 3 depots phase 1."),
    (pids[3], dids[3], "note", "Tom confirmed pilot budget approved by clinical board."),
    (pids[5], dids[5], "email", "Renewal countersigned; kickoff scheduled."),
]
for pid, did, kind, body in activities:
    cur.execute(
        "INSERT INTO activities (tenant_id, contact_id, deal_id, kind, body) "
        "VALUES (%s,%s,%s,%s,%s)", (TENANT, pid, did, kind, body))

approvals = [
    ({"action": "send_email", "to": "dana@birchwoodcap.com",
      "subject": "Updated order form + RLS security docs",
      "body_preview": "Hi Dana — attached are the revised order form and the row-level security overview you asked for."},
     "pipeline-agent", "Dana asked for security docs on the call; deal is in negotiation at $84k.", 84000),
    ({"action": "update_deal", "deal": "Mesa Verde clinic suite", "field": "stage",
      "from": "proposal", "to": "negotiation"},
     "pipeline-agent", "Allie countered on seat count; proposal is now under active negotiation.", 47000),
    ({"action": "issue_quote", "company": "Halcyon Logistics", "amount": 132000,
      "terms": "3-depot phase 1, annual"},
     "quote-agent", "Scoping meeting settled phase-1 size; quote requested by Friday.", 132000),
]
for action, agent, reasoning, value in approvals:
    cur.execute(
        "INSERT INTO approvals (tenant_id, proposed_action, agent, reasoning, value_at_stake, status) "
        "VALUES (%s,%s,%s,%s,%s,'pending')", (TENANT, Json(action), agent, reasoning, value))

cur.execute("SELECT (SELECT count(*) FROM companies), (SELECT count(*) FROM contacts), "
            "(SELECT count(*) FROM deals), (SELECT count(*) FROM activities), "
            "(SELECT count(*) FROM approvals)")
print("seeded (companies, contacts, deals, activities, approvals):", cur.fetchone())
conn.commit()
conn.close()
