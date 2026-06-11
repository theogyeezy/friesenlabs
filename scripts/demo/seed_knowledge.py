"""Seed the demo tenant's KNOWLEDGE corpus into `documents` — chat citations need it.

The hand-authored markdown under `agents/knowledge_seed/` (pricing policy, sales playbooks,
onboarding, FAQs, battlecards) is the tenant's knowledge base. This script lands it in the
pgvector `documents` table so `conv/rag.py` has grounded sources to cite — without a corpus,
the citation invariant drops every claim and chat can't answer knowledge questions.

It REUSES the production ingestion pieces rather than inventing a pipeline:
  * `ingest.chunk.chunk_text` — the same ~400-token windowing the connectors use.
  * `ingest.run_sync.build_embedder` — the same embedder seam: deterministic offline stub by
    default ($0, no AWS), Titan V2 only when INGEST_REAL_STORES=1.
  * `ingest.pipeline.PgDocumentStore` — the same RLS-bound, pooled-per-op, `SET LOCAL`
    tenant-scoped upsert (ON CONFLICT (tenant_id, source, ref_id)), so re-running re-embeds in
    place and never duplicates.

Each doc lands with `source='upload'` and `ref_id = demo:kb:<slug>#<seq>`. The `demo:kb:`
namespace is disjoint from the CRM fixture's `demo:doc:` documents, so this seeder and
`scripts/demo/load_demo_tenant.py` compose in any order without clobbering each other.

Run INSIDE the VPC as a one-off ECS task, or against a reachable crm_app DSN locally:
    TENANT_ID=<uuid> INGEST_REAL_STORES=1 python scripts/demo/seed_knowledge.py
    python scripts/demo/seed_knowledge.py --dsn postgresql://crm_app:...@host/uplift --tenant <uuid>

IMPORT SAFETY: importing this module needs no AWS, boto3, or psycopg2 (lazy in the connect/embed
paths); reading + chunking the corpus is pure and needs only the repo on the path.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_DOCS_DIR = os.path.join(REPO_ROOT, "agents", "knowledge_seed")

# Knowledge docs land as uploads (schema documents.source vocabulary) under a dedicated ref_id
# namespace so they never collide with the CRM fixture's demo:doc: documents.
KB_SOURCE = "upload"
KB_REF_PREFIX = "demo:kb:"


# --------------------------------------------------------------------------- corpus
def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split optional leading `--- key: value --- ` frontmatter from the markdown body.

    Returns (metadata, body). No YAML dependency — a flat key: value block is all these docs use.
    """
    meta: dict[str, str] = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            block = text[3:end].strip()
            for line in block.splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip()] = value.strip()
            body = text[end + 4:].lstrip("\n")
            return meta, body
    return meta, text


def load_corpus(docs_dir: str = DEFAULT_DOCS_DIR) -> list[dict]:
    """Read every `.md` knowledge doc (README excluded) into {slug, title, category, content}.

    `content` is the title + body (frontmatter title folded into the embedded text so retrieval
    sees it); deterministic order by slug so a run is reproducible.
    """
    docs: list[dict] = []
    for fname in sorted(os.listdir(docs_dir)):
        if not fname.endswith(".md") or fname.lower() == "readme.md":
            continue
        slug = fname[:-3]
        with open(os.path.join(docs_dir, fname), encoding="utf-8") as f:
            raw = f.read()
        meta, body = _parse_frontmatter(raw)
        title = meta.get("title", slug)
        # Fold the title into the embedded content so a query like "discount policy" can match
        # the heading even when the body phrases it differently.
        content = f"{title}\n\n{body}".strip()
        docs.append({"slug": slug, "title": title,
                     "category": meta.get("category", "knowledge"), "content": content})
    return docs


def plan_chunks(docs: list[dict]) -> list[dict]:
    """Chunk every doc via the production chunker into upsertable rows.

    Returns [{ref_id, content}] with ref_id = demo:kb:<slug>#<seq> — stable across runs so the
    upsert is idempotent. Pure (no DB / no embedder); the embed+upsert happens in `seed`.
    """
    from ingest.chunk import chunk_text  # noqa: PLC0415 — repo import, no AWS/DB

    rows: list[dict] = []
    for doc in docs:
        pieces = chunk_text(doc["content"])
        for seq, piece in enumerate(pieces):
            rows.append({"ref_id": f"{KB_REF_PREFIX}{doc['slug']}#{seq}", "content": piece})
    return rows


# --------------------------------------------------------------------------- seed
def build_embedder():
    """The ingest embedder seam — offline stub unless INGEST_REAL_STORES=1 (Titan V2)."""
    from ingest.run_sync import build_embedder as _build  # noqa: PLC0415 — lazy (boto3 in real mode)

    return _build()


def seed(store, embedder, *, tenant_id: str, docs_dir: str = DEFAULT_DOCS_DIR) -> dict:
    """Chunk → embed → upsert the knowledge corpus into `store` (a DocumentStore) under
    `tenant_id`. Idempotent via the store's ON CONFLICT upsert. Returns counts.

    `store` is an `ingest.pipeline.DocumentStore` (PgDocumentStore in real use, the in-memory
    fake in tests); `embedder` is `str -> 1024-float vector`.
    """
    from ingest import EMBEDDING_DIM  # noqa: PLC0415

    docs = load_corpus(docs_dir)
    rows = plan_chunks(docs)
    for row in rows:
        vec = embedder(row["content"])
        if len(vec) != EMBEDDING_DIM:
            raise ValueError(f"embedder returned dim {len(vec)} != {EMBEDDING_DIM}")
        # content_hash is derived from content by PgDocumentStore (the schema has no hash column);
        # pass it for protocol-completeness / the in-memory fake.
        import hashlib  # noqa: PLC0415

        chash = hashlib.sha256(row["content"].encode("utf-8")).hexdigest()
        store.upsert(str(tenant_id), KB_SOURCE, row["ref_id"], row["content"], vec, chash)
    return {"docs": len(docs), "chunks": len(rows)}


# --------------------------------------------------------------------------- connection
def build_store():
    """A PgDocumentStore from --dsn / UPLIFT_DB_URL, else Secrets Manager (CRM_APP_SECRET_ARN +
    DB_HOST). psycopg2/boto3 imported lazily; the store itself owns the pooled SET LOCAL txn."""
    from ingest.pipeline import PgDocumentStore  # noqa: PLC0415 — lazy (psycopg2 on construction)

    dsn = os.environ.get("UPLIFT_DB_URL")
    if dsn:
        return PgDocumentStore(dsn)
    if os.environ.get("CRM_APP_SECRET_ARN") and os.environ.get("DB_HOST"):
        import json  # noqa: PLC0415

        import boto3  # noqa: PLC0415

        sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        creds = json.loads(
            sm.get_secret_value(SecretId=os.environ["CRM_APP_SECRET_ARN"])["SecretString"])
        host = os.environ["DB_HOST"]
        port = os.environ.get("DB_PORT", "5432")
        name = os.environ.get("DB_NAME", "uplift")
        dsn = (f"postgresql://{creds['username']}:{creds['password']}@{host}:{port}/{name}")
        return PgDocumentStore(dsn)
    raise SystemExit(
        "no DB connection configured: set UPLIFT_DB_URL, or CRM_APP_SECRET_ARN + DB_HOST")


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed the demo tenant's knowledge corpus (agents/knowledge_seed/*.md) into "
                    "the pgvector documents table via the ingest chunk/embed/upsert path.")
    parser.add_argument("--docs-dir", default=DEFAULT_DOCS_DIR,
                        help=f"knowledge docs directory (default {DEFAULT_DOCS_DIR})")
    parser.add_argument("--tenant", default=None,
                        help="tenant uuid to seed under (default: $TENANT_ID)")
    parser.add_argument("--dsn", default=None,
                        help="crm_app DSN; overrides $UPLIFT_DB_URL for this run")
    args = parser.parse_args(argv)

    tenant = (args.tenant or os.environ.get("TENANT_ID") or "").strip()
    if not tenant:
        parser.error("no tenant id (pass --tenant or set $TENANT_ID)")
    if args.dsn:
        os.environ["UPLIFT_DB_URL"] = args.dsn

    embedder = build_embedder()
    store = build_store()
    counts = seed(store, embedder, tenant_id=tenant, docs_dir=args.docs_dir)

    real = os.environ.get("INGEST_REAL_STORES", "") in ("true", "1")
    sys.stderr.write(
        f"seeded knowledge corpus for tenant {tenant} "
        f"(embedder={'titan-v2' if real else 'offline-stub'}): {counts}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via seed()/main() in tests
    raise SystemExit(main())
