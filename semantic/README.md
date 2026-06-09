# semantic/ — Cube semantic layer (Phase 3)

Cube sits between the agents and the database. Agents query **governed metrics, never raw SQL** —
which makes dashboards correct, answers trustworthy, and tenant isolation enforced one more time
(defense-in-depth over Postgres RLS).

## Contents
- `security.js` — the tenant security context (unit-tested):
  - `queryRewrite(query, { securityContext })` forces a `tenant_id = <jwt tenant>` filter onto every
    cube the query touches, and **throws `no tenant`** if the context is missing/forged.
  - `contextToAppId({ securityContext })` keeps compile/cache resources separate per tenant.
- `cube.js` — Cube config that wires `security.js` in.
- `model/cubes/*.js` — cubes over the CRM tables (`Deals`, `Contacts`, `Companies`, `Activities`):
  measures + dimensions in business language; `tenant_id` is present-but-hidden (`shown: false`) so
  the security layer can filter on it without exposing it.
- `test/security.test.js` — Node built-in test runner; proves the force-filter + throw-without-tenant.

## How agents reach Cube
The agents call a `query_cube` custom tool (Phase 4). The tool runs in your VPC, passes the tenant's
security context (from the verified JWT), and returns governed results. The agent never sees raw SQL
or another tenant's data — Cube + RLS both guarantee it.

## Deploy (infra authored, not applied — needs Nick)
`infra/modules/cube` runs the `cubejs/cube` image as a Fargate service in the shared ECS cluster
(`infra/modules/ecs`), pointed at Aurora over the private SG with Redis as cache/queue driver, and
`CUBEJS_DB_USER/PASS` set to the **crm_app** non-owner role (so RLS applies). Exposed internally only.
Pin the image to a digest and `CUBEJS_API_SECRET` from Secrets Manager before apply.

## Test
```bash
node --test "semantic/test/*.test.js"      # security context
bash scripts/smoke/03_semantic.sh          # syntax-check + tests
```
