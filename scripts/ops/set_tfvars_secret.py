"""The BLESSED way to update PROD_AUTO_TFVARS_B64 — with the clobber guard.

THE INCIDENT (2026-06-12): the canonical machine-local infra/prod.auto.tfvars was re-encoded
into the PROD_AUTO_TFVARS_B64 GitHub secret WITHOUT another lane's staged flags (the REQ-013
dedicated-SG migration) — four deploys then silently planned a REVERT of live security posture
and each hung ~45 minutes on the Lambda-ENI wait. Values changing is normal (flags flip);
a variable NAME disappearing is the clobber signal.

THE GUARD: a key-NAME manifest (names only — they appear in infra/variables.tf anyway; never
values) lives in SSM Parameter Store at /uplift/live/tfvars-keys. Two enforcement points:
  1. This script (encode + set): refuses when the file's keys are not a superset of the
     manifest, unless each removal is explicitly acknowledged with --allow-remove. On success
     it sets the gh secret AND updates the manifest.
  2. deploy.yml (plan job): runs `--check <materialized tfvars>` against the same manifest
     right after decoding the secret — a secret set AROUND this script still fails fast,
     before the state lock, before any apply.

Usage:
  python scripts/ops/set_tfvars_secret.py infra/prod.auto.tfvars              # guard + set + manifest
  python scripts/ops/set_tfvars_secret.py --check infra/prod.auto.tfvars     # guard only (CI)
  python scripts/ops/set_tfvars_secret.py infra/prod.auto.tfvars \
      --allow-remove old_flag,another_flag                                    # acknowledged removal

IMPORT-SAFE: parsing/diff logic is pure; AWS/gh side effects live in main().
"""
from __future__ import annotations

import re

MANIFEST_PARAM = "/uplift/live/tfvars-keys"
SECRET_NAME = "PROD_AUTO_TFVARS_B64"

# A top-level tfvars assignment: optional indent, identifier, '='. Lines inside list/map
# literals are excluded by tracking bracket depth (nested `KEY = value` entries inside a
# map literal must never read as top-level variables).
_ASSIGN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def parse_tfvars_keys(text: str) -> set[str]:
    """The set of top-level variable names assigned in a tfvars file."""
    keys: set[str] = set()
    depth = 0
    for line in text.splitlines():
        stripped = line.split("#", 1)[0]  # comments never contribute (incl. full-line)
        if depth == 0:
            m = _ASSIGN.match(stripped)
            if m:
                keys.add(m.group(1))
        depth += stripped.count("[") + stripped.count("{")
        depth -= stripped.count("]") + stripped.count("}")
        depth = max(depth, 0)
    return keys


def diff_keys(manifest: set[str], current: set[str]) -> tuple[set[str], set[str]]:
    """(missing, added): manifest keys absent from the file, and net-new file keys."""
    return manifest - current, current - manifest


def check(manifest: set[str], current: set[str],
          allow_remove: set[str] | None = None) -> tuple[bool, str]:
    """The guard verdict. Missing manifest keys block unless each is acknowledged."""
    missing, added = diff_keys(manifest, current)
    unacked = missing - (allow_remove or set())
    if unacked:
        return False, (
            "CLOBBER GUARD: the tfvars is MISSING variables that the live secret manifest "
            f"carries: {', '.join(sorted(unacked))}. Another lane likely staged these — "
            "merge their canonical-file edits first (diff against the live task-def/SG state), "
            "or acknowledge a deliberate removal with --allow-remove."
        )
    notes = []
    if missing:
        notes.append(f"acknowledged removals: {', '.join(sorted(missing))}")
    if added:
        notes.append(f"new keys: {', '.join(sorted(added))}")
    return True, "; ".join(notes) or "clean"


# --------------------------------------------------------------------------- side effects
# AWS access shells out to the aws CLI (like the gh call below): the script must work on any
# machine where deploys are operated — laptop credential providers (e.g. `aws login`) that
# boto3 can't load without extras work fine through the CLI, and the CI runner is identical.
def _read_manifest() -> set[str]:
    import subprocess

    res = subprocess.run(
        ["aws", "ssm", "get-parameter", "--name", MANIFEST_PARAM,
         "--query", "Parameter.Value", "--output", "text"],
        capture_output=True, text=True)
    if res.returncode != 0:
        if "ParameterNotFound" in res.stderr:
            return set()  # bootstrap: first run must not block
        raise RuntimeError(f"manifest read failed: {res.stderr.strip()}")
    return {k for k in res.stdout.strip().split(",") if k}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import base64
    import subprocess
    import sys

    p = argparse.ArgumentParser(description="Guarded PROD_AUTO_TFVARS_B64 update.")
    p.add_argument("tfvars", help="path to the canonical prod.auto.tfvars")
    p.add_argument("--check", action="store_true",
                   help="guard only — no secret write, no manifest update (the CI mode)")
    p.add_argument("--allow-remove", default="",
                   help="comma-separated variable names whose removal is deliberate")
    args = p.parse_args(argv)

    text = open(args.tfvars).read()
    current = parse_tfvars_keys(text)
    manifest = _read_manifest()

    ok, msg = check(manifest, current,
                    allow_remove={k.strip() for k in args.allow_remove.split(",") if k.strip()})
    print(("OK: " if ok else "BLOCKED: ") + msg, file=sys.stderr)
    if not ok:
        return 1
    if args.check:
        return 0

    encoded = base64.b64encode(text.encode()).decode()
    subprocess.run(["gh", "secret", "set", SECRET_NAME], input=encoded.encode(), check=True)
    subprocess.run(
        ["aws", "ssm", "put-parameter", "--name", MANIFEST_PARAM,
         "--value", ",".join(sorted(current)), "--type", "String", "--overwrite"],
        check=True, capture_output=True)
    print(f"secret set + manifest updated ({len(current)} keys)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
