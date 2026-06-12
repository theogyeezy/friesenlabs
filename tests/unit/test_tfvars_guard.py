"""Unit: scripts/ops/set_tfvars_secret.py — the tfvars-secret clobber guard.

The 2026-06-12 incident: the canonical machine-local prod.auto.tfvars was re-encoded into
PROD_AUTO_TFVARS_B64 WITHOUT another lane's staged flags (REQ-013 dedicated SGs) — four
deploys then silently planned a REVERT of live security posture and hung 45 minutes on the
Lambda-ENI wait. Values change legitimately (flags flip); KEY REMOVALS are the danger signal.

The guard: a key-NAME manifest (names only — they appear in the repo anyway, never values)
lives in SSM (/uplift/live/tfvars-keys). The blessed setter script refuses to encode a file
whose keys aren't a superset of the manifest (unless removals are explicitly acknowledged),
and deploy.yml runs the same check against the MATERIALIZED tfvars before planning, so a
secret set around the script still fails fast — before the state lock, before any apply.
"""
from __future__ import annotations

import pytest

from scripts.ops.set_tfvars_secret import diff_keys, parse_tfvars_keys

TFVARS = """
# comment with an assignment-looking string: foo = bar
api_image         = "1234.dkr.ecr.us-east-1.amazonaws.com/uplift-api:abc"   # inline comment
worker_deployed = true

web_callback_urls = [
  "http://localhost:5173/auth/callback",
]
stripe_module_price_ids = { STRIPE_PRICE_ID_MODULE_INTEGRATION = "price_x" }
playbook_dispatch_enabled = true
  indented_key = "also counts"
"""


@pytest.mark.unit
def test_parse_extracts_top_level_keys_only():
    keys = parse_tfvars_keys(TFVARS)
    assert keys == {
        "api_image", "worker_deployed", "web_callback_urls",
        "stripe_module_price_ids", "playbook_dispatch_enabled", "indented_key",
    }
    # Comments never contribute keys; nested map keys (STRIPE_PRICE_ID_...) and
    # list members never leak in as top-level variables.
    assert "foo" not in keys
    assert "STRIPE_PRICE_ID_MODULE_INTEGRATION" not in keys


@pytest.mark.unit
def test_diff_flags_removals_and_reports_additions():
    manifest = {"a", "b", "c"}
    missing, added = diff_keys(manifest, current={"a", "c", "d"})
    assert missing == {"b"}       # the clobber signal — must block
    assert added == {"d"}         # informational — staging new flags is normal


@pytest.mark.unit
def test_diff_clean_when_superset():
    missing, added = diff_keys({"a", "b"}, current={"a", "b", "c"})
    assert missing == set() and added == {"c"}


@pytest.mark.unit
def test_allow_remove_acknowledges_specific_keys_only():
    from scripts.ops.set_tfvars_secret import check

    # The incident shape: a file missing two staged flags must FAIL the check...
    ok, msg = check(manifest={"a", "b", "worker_dedicated_sg"}, current={"a", "b"})
    assert not ok and "worker_dedicated_sg" in msg
    # ...unless the operator explicitly acknowledges THAT key's removal.
    ok, _ = check(manifest={"a", "b", "worker_dedicated_sg"}, current={"a", "b"},
                  allow_remove={"worker_dedicated_sg"})
    assert ok
    # An acknowledgement for a DIFFERENT key does not cover it.
    ok, msg = check(manifest={"a", "b", "worker_dedicated_sg"}, current={"a", "b"},
                    allow_remove={"unrelated"})
    assert not ok and "worker_dedicated_sg" in msg


@pytest.mark.unit
def test_empty_manifest_bootstraps_clean():
    from scripts.ops.set_tfvars_secret import check

    ok, _ = check(manifest=set(), current={"a"})
    assert ok  # first run (no manifest yet) must not block
