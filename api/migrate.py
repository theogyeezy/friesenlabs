"""One-off DB migration entrypoint — run as an ECS task INSIDE the VPC to reach the private Aurora.

Connects as the Aurora master (RDS-managed secret), loads db/schema.sql + db/roles.sql, and sets the
crm_app role's password to the value already stored in the crm-app-db secret (so the API, which reads
that same secret, can connect as the non-owner crm_app role with RLS).

Run with: `python -m api.migrate`  (the api image bundles db/). Env:
  DB_HOST, DB_NAME (default uplift), DB_PORT (default 5432),
  AURORA_MASTER_SECRET_ARN, CRM_APP_SECRET_ARN, AWS_REGION.
"""
from __future__ import annotations

import json
import os

import boto3
import psycopg2


def _secret(sm, arn: str) -> dict:
    return json.loads(sm.get_secret_value(SecretId=arn)["SecretString"])


def main() -> None:
    region = os.environ.get("AWS_REGION", "us-east-1")
    sm = boto3.client("secretsmanager", region_name=region)

    host = os.environ["DB_HOST"]
    dbname = os.environ.get("DB_NAME", "uplift")
    port = int(os.environ.get("DB_PORT", "5432"))

    master = _secret(sm, os.environ["AURORA_MASTER_SECRET_ARN"])
    crm = _secret(sm, os.environ["CRM_APP_SECRET_ARN"])
    crm_pw = crm["password"]

    conn = psycopg2.connect(host=host, port=port, dbname=dbname,
                            user=master["username"], password=master["password"])
    conn.autocommit = True  # DDL + CREATE EXTENSION need autocommit
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with conn.cursor() as cur:
        cur.execute(open(os.path.join(here, "db", "schema.sql")).read())
        cur.execute(open(os.path.join(here, "db", "roles.sql")).read())
        cur.execute("ALTER ROLE crm_app PASSWORD %s", (crm_pw,))
    conn.close()
    print("migrate: schema + roles loaded; crm_app password set to match the crm-app-db secret.")


if __name__ == "__main__":
    main()
