"""Unit: scripts/ops/load_workspace_keys.py — pure parsing (no DB, no AWS, no Anthropic)."""
import hashlib
import importlib.util
import os

import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "..",
                       "scripts", "ops", "load_workspace_keys.py")


def _mod():
    spec = importlib.util.spec_from_file_location("load_workspace_keys", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.unit
def test_parse_key_only_and_workspace_tab_key_lines():
    mod = _mod()
    entries = mod.parse_lines([
        "# pre-minted 2026-06-10\n",
        "\n",
        "sk-ant-api03-AAAA\n",
        "wrkspc_123\tsk-ant-api03-BBBB\n",
    ])
    assert [e["workspace_id"] for e in entries] == [None, "wrkspc_123"]
    assert [e["key"] for e in entries] == ["sk-ant-api03-AAAA", "sk-ant-api03-BBBB"]
    # The hash is the dedupe key (ON CONFLICT target); the hint is non-secret (last 4).
    assert entries[0]["key_hash"] == hashlib.sha256(b"sk-ant-api03-AAAA").hexdigest()
    assert entries[0]["key_hint"] == "AAAA"
    assert all(len(e["key_hint"]) == 4 for e in entries)


@pytest.mark.unit
def test_parse_rejects_malformed_key_without_leaking_it():
    mod = _mod()
    with pytest.raises(ValueError) as exc:
        mod.parse_lines(["sk-ant bad key with spaces\n"])
    # The error message must never carry the (potential) key material — hint only.
    assert "sk-ant bad key" not in str(exc.value)


@pytest.mark.unit
def test_parse_empty_input_yields_nothing():
    assert _mod().parse_lines([]) == []
