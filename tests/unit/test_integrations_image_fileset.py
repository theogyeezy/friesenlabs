"""Regression: the API must boot WITHOUT the ingest/ package present.

The production API image (api/Dockerfile) COPYies shared/ api/ agents/ conv/
signup/ ml/ db/ scripts/ — NOT ingest/. PR #67 shipped a top-level
`from ingest...` import in api/integrations_routes.py that pytest could not
catch (the repo root has ingest/), and the deployed container would have
crash-looped at boot. This test simulates the image fileset by installing a
meta-path blocker for `ingest` in a SUBPROCESS (so the parent test run's
already-imported ingest modules can't mask the bug) and asserts:

  1. `import api.app` and `import api.asgi` succeed,
  2. an app builds with default deps,
  3. the sync runner degrades to None (route would answer the honest 503).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

_PROBE = r"""
import sys

class _BlockIngest:
    # Simulates the production image fileset: ingest/ is not COPYed.
    def find_module(self, fullname, path=None):  # legacy hook (harmless)
        return None
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "ingest" or fullname.startswith("ingest."):
            raise ModuleNotFoundError(f"No module named {fullname!r} (image fileset)")
        return None

sys.meta_path.insert(0, _BlockIngest())
for mod in [m for m in list(sys.modules) if m == "ingest" or m.startswith("ingest.")]:
    del sys.modules[mod]

# 1+2: the deployed entrypoint chain must import and build cleanly.
import api.app  # noqa: E402
import api.asgi  # noqa: E402  (module bottom runs build_app())

# 3: the default-built integrations deps must degrade, not crash.
from api.integrations_routes import build_integrations_deps  # noqa: E402

deps = build_integrations_deps()
assert deps.sync_runner is None, "sync_runner must be None when ingest/ is absent"
print("IMAGE-FILESET-BOOT-OK")
"""


@pytest.mark.unit
def test_api_boots_without_ingest_package():
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO)},
    )
    assert proc.returncode == 0, (
        f"API failed to boot without ingest/ (the Docker-image fileset):\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "IMAGE-FILESET-BOOT-OK" in proc.stdout
