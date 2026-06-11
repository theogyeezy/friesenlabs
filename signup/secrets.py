"""The provisioning-side Secrets Manager seam (issue: workspace-key material must live in
Secrets Manager, never in Postgres).

Provisioning's `secrets` dependency historically used three operations:
  * ``exists(ref) -> bool``  — is a secret already stored at this ref? (idempotency guard)
  * ``put(ref, value)``      — store secret material at this ref
  * ``get(ref) -> str``      — read secret material back (NEW: the pool now hands provisioning a
                               Secrets Manager *reference*, not the key, so the key material has
                               to be resolved from SM at consume time)

The real adapter (:class:`Boto3ProvisioningSecrets`) is a thin composition over the two existing,
already-VERIFY-flagged Secrets Manager seams so we do not re-implement (or re-audit) boto3 calls:
  * write/exists  -> api.integrations_routes.Boto3SecretWriter (put_secret_value + create_secret
                     fallback; describe_secret for existence — never reads the value)
  * read          -> ingest.connectors.base.Boto3SecretProvider (get_secret_value)

IMPORT SAFETY: importing this module touches no AWS/boto3 — the underlying clients are lazy
(boto3 imported on first call). Tests inject a fake `client` (a single object that answers all of
describe_secret / put_secret_value / create_secret / get_secret_value, exactly like the AWS SDK
client) so one stub backs both the read and write sides.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ProvisioningSecrets(Protocol):
    """The `secrets` seam Provisioner depends on (write + existence + read)."""

    def exists(self, ref: str) -> bool: ...

    def put(self, ref: str, value: str) -> None: ...

    def get(self, ref: str) -> str: ...


class Boto3ProvisioningSecrets:
    """Real Secrets Manager seam for provisioning — composes the existing write + read clients.

    A single boto3 secretsmanager client backs both halves: it is built lazily and shared, so a
    real deploy makes exactly one client. Tests pass ``client=<fake>`` and that same fake is wired
    into both the writer and the reader, so a put is visible to a subsequent get.
    """

    def __init__(self, *, region: str | None = None, client: Any = None) -> None:
        # Import lazily so this module stays import-safe (no AWS at import); both seams are too.
        from api.integrations_routes import Boto3SecretWriter  # noqa: PLC0415
        from ingest.connectors.base import Boto3SecretProvider  # noqa: PLC0415

        self._writer = Boto3SecretWriter(region=region, client=client)
        self._reader = Boto3SecretProvider(region=region, client=client)

    def exists(self, ref: str) -> bool:
        return self._writer.secret_exists(ref)

    def put(self, ref: str, value: str) -> None:
        self._writer.put_secret(ref, value)

    def get(self, ref: str) -> str:
        return self._reader.get_secret(ref)
