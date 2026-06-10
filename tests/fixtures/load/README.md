# Load tier — performance fixture (generated on demand, not committed)

The `load` test pack is **2000 companies / 6000 contacts / 3000 deals** (~22k activities + ~22k
mirrored documents). It exists for performance testing — seed timing, query plans, embed-pass
throughput, dashboard aggregation under volume.

It is **not committed** (~19MB JSON / ~16MB SQL). It is deterministic (`--tier load`, seed 97,
fixed anchor), so regenerating is the source of truth, not a stored blob:

```bash
python scripts/generate_test_packs.py --tier load          # writes dataset.{json,sql} here
python scripts/generate_test_packs.py --tier load --format json
```

The test suite (`tests/unit/test_generate_test_packs.py`) regenerates the load pack into a temp
dir and asserts it builds in **under 30s** and serializes to **under 50MB** — so a regression in
generation cost is caught in CI without committing the artifact.

## Seeding it into a tenant (PREPARED — do not run against live without Lane Nick)

The `--format sql` output is one idempotent, RLS-scoped transaction meant to run **inside the
VPC as the non-owner `crm_app` role** (the `seed_demo_tenant.py` pattern). It is generated with
a distinct, fixed demo tenant uuid per tier so a load pack never collides with the smoke/demo
tenants. `documents.embedding` is left NULL — a Titan V2 embed pass via the ingest embedder seam
must backfill it before RAG retrieval works.

```bash
# PREPARED reference only — live mutation is Lane Nick, via the uplift-migrate-oneoff task family.
psql -v ON_ERROR_STOP=1 -f tests/fixtures/load/dataset.sql   # as crm_app, inside the VPC
```
