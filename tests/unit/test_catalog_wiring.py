"""Unit: the governed-catalog wiring for SavedViews member validation (#195's follow-up).

PR #195 shipped `semantic/model/catalog.json` + `shared.semantic_catalog` but deliberately
left them UNWIRED — the api image didn't ship the file, so `SavedViews(allowed_members=...)`
in `api/asgi.py` would have been a silent no-op. This lane closes the loop:

  * api/Dockerfile now COPYies semantic/model/catalog.json into the image fileset;
  * api/asgi.py resolves the catalog at boot (`_catalog_allowed_members`) and threads it into
    BOTH SavedViews constructions (Pg-backed and in-memory);
  * FALLBACK: a missing catalog (older image / stripped fileset) skips validation — exactly
    the pre-#195 behavior — and emits a STRUCTURED warning (event=semantic_catalog_missing)
    so the weakening is visible in CloudWatch, never silent.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

import api.asgi as asgi
from shared.semantic_catalog import catalog_members

REPO = Path(__file__).resolve().parents[2]


# ---------------- the resolver + fallback ----------------
@pytest.mark.unit
def test_catalog_members_resolve_from_the_committed_catalog(caplog):
    with caplog.at_level(logging.WARNING, logger="api.asgi"):
        members = asgi._catalog_allowed_members()
    assert members == catalog_members()      # the real governed set, not a hand-typed copy
    assert "Deals.pipeline_value" in members  # a known governed member
    assert not [r for r in caplog.records if getattr(r, "event", "") == "semantic_catalog_missing"]


@pytest.mark.unit
def test_missing_catalog_skips_validation_with_a_structured_warning(monkeypatch, caplog):
    monkeypatch.setattr(asgi, "catalog_members_or_none", lambda: None)
    with caplog.at_level(logging.WARNING, logger="api.asgi"):
        members = asgi._catalog_allowed_members()
    assert members is None  # SavedViews(allowed_members=None) == validation skipped
    warnings = [r for r in caplog.records
                if getattr(r, "event", "") == "semantic_catalog_missing"]
    assert len(warnings) == 1, "exactly one structured warning must mark the degraded mode"
    rec = warnings[0]
    assert rec.levelno == logging.WARNING
    assert rec.catalog_path  # the probe path rides the record for forensics
    assert "member validation is OFF" in rec.getMessage()


# ---------------- build_app threads it into SavedViews ----------------
@pytest.mark.unit
def test_build_app_wires_allowed_members_into_saved_views(monkeypatch):
    """Catch the silent-no-op regression: a build_app() must construct SavedViews WITH the
    resolved catalog (both DSN branches share the resolver call up front; the no-DSN branch is
    the one buildable offline)."""
    captured: dict = {}
    real_saved_views = asgi.SavedViews

    def spying_saved_views(*args, **kwargs):
        captured["allowed_members"] = kwargs.get("allowed_members")
        return real_saved_views(*args, **kwargs)

    monkeypatch.setattr(asgi, "SavedViews", spying_saved_views)
    monkeypatch.setattr(asgi, "dsn_from_env", lambda: None)  # the offline /healthz-only boot
    asgi.build_app()
    assert captured["allowed_members"] == catalog_members()


@pytest.mark.unit
def test_build_app_still_boots_when_the_catalog_is_absent(monkeypatch):
    """The fallback is load-bearing: an image without the catalog must boot (validation off),
    never crash at import/boot time."""
    monkeypatch.setattr(asgi, "catalog_members_or_none", lambda: None)
    monkeypatch.setattr(asgi, "dsn_from_env", lambda: None)
    app = asgi.build_app()
    assert any(r.path == "/healthz" for r in app.routes)


# ---------------- the image actually ships the file ----------------
@pytest.mark.unit
def test_api_dockerfile_copies_the_catalog():
    """The wiring only means something if the file is IN the image fileset (the
    semantic_catalog.py deployment note). Assert the COPY line so a Dockerfile refactor that
    drops it fails here instead of silently disabling member validation in prod."""
    dockerfile = (REPO / "api" / "Dockerfile").read_text()
    assert re.search(
        r"^COPY\s+semantic/model/catalog\.json\s+\./semantic/model/catalog\.json\s*$",
        dockerfile, flags=re.MULTILINE,
    ), "api/Dockerfile must COPY semantic/model/catalog.json (SavedViews member validation)"


@pytest.mark.unit
def test_catalog_path_matches_the_image_layout():
    """shared/semantic_catalog.py derives the path as <repo-root>/semantic/model/catalog.json;
    the Dockerfile must land the file exactly there relative to /app (where shared/ lives)."""
    from shared.semantic_catalog import CATALOG_PATH
    assert Path(CATALOG_PATH) == REPO / "semantic" / "model" / "catalog.json"
    assert Path(CATALOG_PATH).is_file()
