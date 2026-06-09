# Brief: Phase 1 — The Data Plane (Aurora + pgvector, RLS, schema, S3, Redis)

## Goal
Stand up the system-of-record + vector store + object lake + cache, with **tenant isolation born
here**. Get RLS right and everything downstream inherits it. AUTHOR + VALIDATE IaC only (no apply);
the SQL schema + the isolation proof are the real, runnable deliverables.

## Owner
Orchestrator (data plane is cross-cutting security — keep it in-house). Files live in `infra/` and a
new top-level `db/`. Do not edit `web/` (the FE agent owns it).

## Files to create
- `infra/modules/data/main.tf` — Aurora PostgreSQL **Serverless v2** cluster:
  engine `aurora-postgresql` 16.8, `serverless-v2-scaling MinCapacity=1` (NOT 0.5 — starves HNSW),
  `MaxCapacity=16`, `--manage-master-user-password` (master cred → Secrets Manager, never echoed),
  `storage-encrypted`, CloudWatch logs export, in `SG_DB`, across the two private subnets
  (db subnet group). One `db.serverless` instance.
- `infra/modules/redis/main.tf` — ElastiCache (Valkey) `cache.t4g.small`, transit + at-rest
  encryption, in `SG_REDIS`, private subnets.
- `infra/modules/s3/main.tf` — two buckets (`datalake`, `uploads`): block-public-access, SSE-KMS,
  versioning enabled, TLS-only bucket policy. Objects are prefixed by `tenant_id` at write time.
- wire all three modules into `infra/main.tf`; add outputs. `terraform fmt` + `validate` must pass.
- `db/schema.sql` — the schema (below).
- `db/roles.sql` — `crm_app` non-owner login role + GRANTs.
- `db/README.md` — how to apply (psql against the cluster) and the RLS contract.

## Schema (`db/schema.sql`) — Build Guide §Steps 10–12
- `CREATE EXTENSION IF NOT EXISTS vector;`
- `documents(id, tenant_id uuid NOT NULL, source, ref_id, content, embedding vector(1024))`
  + `hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=128)` + `(tenant_id, source)` idx.
- Core CRM (all with `tenant_id uuid NOT NULL` + same RLS): `contacts`, `companies`,
  `deals` (pipeline stage), `activities`.
- `saved_views(tenant_id, view_id, version, spec_json jsonb, semantic_refs, source_prompt, created_by)`.
- `approvals(tenant_id, proposed_action, agent, reasoning, value_at_stake, status)` — Greenlight queue.
- `traces(tenant_id, ...)` — per-step agent decision traces.

## RLS — the single most important step (Build Guide §Step 11, red box)
For EVERY tenant-scoped table:
```sql
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE  ROW LEVEL SECURITY;   -- without FORCE the owner bypasses RLS
CREATE POLICY tenant_isolation ON <t>
  USING      (tenant_id = current_setting('app.current_tenant', true)::uuid)
  WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
```
The app/worker connects as **`crm_app`** (plain, non-owning — NOT the table owner, NOT a
superuser/BYPASSRLS role) and does `SET app.current_tenant = %s` per checked-out connection,
`RESET` after. Note: `scripts/isolation_test.py` currently uses GUC `app.tenant_id`; **reconcile the
GUC name to `app.current_tenant`** to match the policy. Add the pgvector iterative-scan settings for
tenant-filtered ANN: `SET hnsw.iterative_scan='relaxed_order'; SET hnsw.max_scan_tuples=20000;`.

## Tests (the gate)
- **Integration** `tests/integration/test_rls_isolation.py`: spin up Postgres+pgvector locally
  (docker `pgvector/pgvector:pg16` if docker is available; else mark the test `skip` with a clear
  reason). Apply `db/schema.sql` + `db/roles.sql`, connect as `crm_app`, insert rows for two
  tenants, assert each tenant sees only its own — **including a vector similarity (`ORDER BY
  embedding <=> ...`) query**. Prove cross-tenant SELECT and UPDATE both return nothing.
- Update `scripts/isolation_test.py` to run this same proof against `UPLIFT_DB_URL` (real gate once
  applied). Add a smoke `scripts/smoke/01_data_plane.sh` (schema parses via `psql -f` against a
  throwaway DB, or sqlfluff/`psql --dry` if no DB; skip cleanly if neither).
- `terraform validate` clean.

## Constraints
- **No apply / no live AWS.** Mark the Aurora/Redis/S3 apply as `BLOCKED: needs Nick` in BUILD_STATUS.
- No secrets in repo (master cred is RDS-managed → Secrets Manager). Do not run `git`.
- Lock `vector(1024)` (Titan V2) — changing dims later forces a full re-embed.

## Done when
`terraform validate` passes with the three new modules; `db/schema.sql` + `db/roles.sql` express the
full tenant-scoped schema with FORCE'd RLS on every table; the RLS isolation integration test passes
locally (or skips cleanly with a logged reason if docker is absent) AND proves both row and vector
queries are tenant-scoped; BUILD_STATUS Phase 1 row updated with test + review status.
