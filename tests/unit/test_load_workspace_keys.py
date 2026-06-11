"""Unit: scripts/ops/load_workspace_keys.py — parsing + the secret-write seam (no DB, no AWS).

The loader writes key MATERIAL to Secrets Manager and stores only a NON-SECRET reference in the
pool table. These pins:
  * parse derives a deterministic Secrets Manager reference from each key's hash (idempotent), and
    never lets material flow toward the DB (the `_db_entries` projection drops it);
  * a fake SecretWriter receives the material under the derived reference;
  * the loader FAILS CLOSED — without LOAD_KEYS_REAL_SECRETS (and no injected writer) it refuses to
    write pool rows, so it can never seed a row referencing a secret it did not write;
  * --dry-run touches neither AWS nor the DB.
"""
import hashlib
import importlib.util
import io
import os

import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "..",
                       "scripts", "ops", "load_workspace_keys.py")


def _mod():
    spec = importlib.util.spec_from_file_location("load_workspace_keys", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeWriter:
    def __init__(self):
        self.sm = {}

    def put_secret(self, ref, value):
        self.sm[ref] = value


@pytest.mark.unit
def test_parse_derives_reference_from_hash_and_keeps_hint():
    mod = _mod()
    entries = mod.parse_lines([
        "# pre-minted 2026-06-10\n",
        "\n",
        "sk-ant-api03-AAAA\n",
        "wrkspc_123\tsk-ant-api03-BBBB\n",
    ])
    assert [e["workspace_id"] for e in entries] == [None, "wrkspc_123"]
    # The hash is the dedupe key (ON CONFLICT target); the hint is non-secret (last 4).
    h0 = hashlib.sha256(b"sk-ant-api03-AAAA").hexdigest()
    assert entries[0]["key_hash"] == h0
    assert entries[0]["key_hint"] == "AAAA"
    assert all(len(e["key_hint"]) == 4 for e in entries)
    # The Secrets Manager reference is derived from the hash (deterministic -> idempotent), lives
    # under the pool prefix, and is NOT the material.
    assert entries[0]["secret_ref"] == f"uplift/pool/anthropic_key/{h0[:16]}"
    assert "sk-ant" not in entries[0]["secret_ref"]


@pytest.mark.unit
def test_db_entries_strip_key_material():
    mod = _mod()
    entries = mod.parse_lines(["sk-ant-api03-AAAA\n"])
    db_rows = mod._db_entries(entries)
    assert "key" not in db_rows[0]                      # material never reaches the DB projection
    assert set(db_rows[0]) == {"secret_ref", "key_hash", "key_hint", "workspace_id"}


@pytest.mark.unit
def test_write_secrets_sends_material_to_secrets_manager_under_reference():
    mod = _mod()
    entries = mod.parse_lines(["wrkspc_1\tsk-ant-api03-CCCC\n"])
    w = _FakeWriter()
    mod.write_secrets(entries, w)
    ref = entries[0]["secret_ref"]
    assert w.sm[ref] == "sk-ant-api03-CCCC"             # material is in SM, keyed by the reference


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


@pytest.mark.unit
def test_main_dry_run_touches_no_aws_or_db(monkeypatch, capsys):
    mod = _mod()
    monkeypatch.setattr(mod.sys, "stdin", io.StringIO("sk-ant-api03-AAAA\n"))
    # A writer that explodes if used — dry-run must never call it.
    boom = type("Boom", (), {"put_secret": lambda *a: (_ for _ in ()).throw(AssertionError())})()
    rc = mod.main(["--dry-run"], writer=boom)
    assert rc == 0
    assert "no secrets written" in capsys.readouterr().out


@pytest.mark.unit
def test_main_fails_closed_without_real_secrets_switch(monkeypatch, capsys):
    mod = _mod()
    monkeypatch.delenv("LOAD_KEYS_REAL_SECRETS", raising=False)
    monkeypatch.setattr(mod.sys, "stdin", io.StringIO("sk-ant-api03-AAAA\n"))
    rc = mod.main([])   # no injected writer, switch off -> refuse
    assert rc == 1
    assert "refusing to load" in capsys.readouterr().err
