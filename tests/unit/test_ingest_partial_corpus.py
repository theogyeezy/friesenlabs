"""Unit: sync_tenant reports PARTIAL status on a mid-sync embed/upsert failure instead of either
crashing the whole run or silently dropping the failed docs.

Contract:
  * a per-doc embed/upsert failure is isolated — the rest of the corpus still lands (succeeded vs
    failed are both counted; failed refs are reported).
  * the cursor is HELD at `since` on any failure, so the next run re-pulls the window and retries
    the failed docs (the already-succeeded ones skip cheaply via the content hash).
  * a fully-clean run still advances the cursor and reports status "ok" (no regression).
  * a cross-tenant record stays a HARD raise (a tenancy violation is never a recoverable skip).
"""
import pytest

from ingest import EMBEDDING_DIM
from ingest.pipeline import (
    InMemoryCursorStore,
    InMemoryDocumentStore,
    SyncResult,
    sync_tenant,
)
from ingest.connectors.base import LandResult, NormalizedRecord

TENANT = "11111111-1111-1111-1111-111111111111"
SOURCE = "fake"


def _good_vec(_text: str) -> list:
    return [0.01] * EMBEDDING_DIM


class _Connector:
    """A minimal Connector: pulls a fixed list of records, lands them as a no-op."""

    source = SOURCE

    def __init__(self, records):
        self._records = records

    def authenticate(self):
        return None

    def pull(self, since):
        return list(self._records)

    def land(self, records):
        return LandResult(rows_upserted=len(records))


def _rec(ref_id, text, updated_at):
    # One short summary block -> exactly one chunk whose doc_ref_id == the record ref_id.
    return NormalizedRecord(
        tenant_id=TENANT, source=SOURCE, ref_id=ref_id, table="contacts",
        row={}, raw={}, text_blocks=[{"kind": "summary", "text": text}], updated_at=updated_at,
    )


def _records():
    return [
        _rec("a", "alpha doc", "2026-01-01T00:00:00Z"),
        _rec("b", "bravo doc", "2026-01-02T00:00:00Z"),
        _rec("c", "charlie doc", "2026-01-03T00:00:00Z"),
    ]


@pytest.mark.unit
def test_clean_sync_is_ok_and_advances_cursor():
    store, cursors = InMemoryDocumentStore(), InMemoryCursorStore()
    res = sync_tenant(TENANT, _Connector(_records()), _good_vec, store, cursors)
    assert res.embedded == 3 and res.failed == 0
    assert res.partial is False and res.status == "ok"
    assert res.cursor == "2026-01-03T00:00:00Z"
    assert cursors.get(TENANT, SOURCE) == "2026-01-03T00:00:00Z"


@pytest.mark.unit
def test_mid_sync_failure_reports_partial_and_does_not_crash():
    # The embedder blows up on doc "b" only — the run must still embed a + c and report b as failed.
    def flaky(text):
        if text == "bravo doc":
            raise RuntimeError("Bedrock ThrottlingException")
        return _good_vec(text)

    store, cursors = InMemoryDocumentStore(), InMemoryCursorStore()
    res = sync_tenant(TENANT, _Connector(_records()), flaky, store, cursors)

    assert res.embedded == 2          # a and c still landed
    assert res.failed == 1            # b failed
    assert res.failed_ref_ids == ["b"]
    assert res.partial is True and res.status == "partial"
    # The two good docs are actually persisted.
    assert store.get_content_hash(TENANT, SOURCE, "a") is not None
    assert store.get_content_hash(TENANT, SOURCE, "c") is not None
    assert store.get_content_hash(TENANT, SOURCE, "b") is None


@pytest.mark.unit
def test_partial_sync_holds_cursor_so_failed_docs_retry():
    def flaky(text):
        if text == "bravo doc":
            raise RuntimeError("transient")
        return _good_vec(text)

    store, cursors = InMemoryDocumentStore(), InMemoryCursorStore()
    res = sync_tenant(TENANT, _Connector(_records()), flaky, store, cursors)
    # Cursor NOT advanced (held at the pre-sync value None) so the next run re-pulls + retries.
    assert res.cursor is None
    assert cursors.get(TENANT, SOURCE) is None

    # Second run with a now-healthy embedder: b is retried + embedded; a/c skip via the hash.
    res2 = sync_tenant(TENANT, _Connector(_records()), _good_vec, store, cursors)
    assert res2.failed == 0 and res2.partial is False
    assert res2.embedded == 1 and res2.skipped == 2   # only b needed embedding
    assert res2.cursor == "2026-01-03T00:00:00Z"      # clean run finally advances the cursor


@pytest.mark.unit
def test_bad_embedding_dim_is_a_partial_failure_not_a_crash():
    res = sync_tenant(
        TENANT, _Connector([_rec("a", "alpha", "2026-01-01T00:00:00Z")]),
        lambda _t: [0.0] * (EMBEDDING_DIM - 1),  # wrong dimension
        InMemoryDocumentStore(), InMemoryCursorStore(),
    )
    assert res.failed == 1 and res.embedded == 0 and res.partial is True
    assert res.failed_ref_ids == ["a"]


@pytest.mark.unit
def test_cross_tenant_record_still_hard_raises():
    bad = NormalizedRecord(
        tenant_id="99999999-9999-9999-9999-999999999999", source=SOURCE, ref_id="x",
        table="contacts", row={}, raw={}, text_blocks=[{"kind": "summary", "text": "x"}],
        updated_at="2026-01-01T00:00:00Z",
    )
    with pytest.raises(ValueError, match="cross-tenant"):
        sync_tenant(TENANT, _Connector([bad]), _good_vec,
                    InMemoryDocumentStore(), InMemoryCursorStore())
