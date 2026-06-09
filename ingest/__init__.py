"""Uplift ingestion plane — connectors → chunk → embed → upsert into pgvector.

A background worker owns this package. It pulls a tenant's world from a source
(HubSpot first), lands raw JSON to S3 + normalized rows to Aurora, chunks for
retrieval, embeds with Titan V2 (1024 dims), and upserts into `documents`.

IMPORT SAFETY: importing any module here must NOT require AWS or a database.
Every external client (HubSpot, Bedrock, Postgres) is an injected interface;
real clients are constructed lazily, only when actually invoked.
"""
from __future__ import annotations

# Locked to match db/schema.sql `documents.embedding vector(1024)`.
# Changing this forces a full re-embed — do not change lightly.
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIM = 1024

__all__ = ["EMBEDDING_MODEL_ID", "EMBEDDING_DIM"]
