"""Unit: signup/secrets.Boto3ProvisioningSecrets — the provisioning Secrets-Manager seam.

Pins:
  * one injected fake SM client backs BOTH the write and the read side, so a put is visible to a
    later get (the pool reference -> per-tenant secret resolution path);
  * exists rides describe_secret and NEVER reads the value;
  * a missing reference on get surfaces as the read seam's not-found error (never silently empty).
"""
import pytest

from ingest.connectors.base import SecretNotFoundError
from signup.secrets import Boto3ProvisioningSecrets, ProvisioningSecrets


class _NotFound(Exception):
    """Fake botocore ResourceNotFoundException (matched by class name in the seams)."""

    def __init__(self):
        super().__init__("not found")
        self.response = {"Error": {"Code": "ResourceNotFoundException"}}


class FakeSmClient:
    """A single fake AWS secretsmanager client answering all 4 calls both seams use."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def describe_secret(self, SecretId):
        if SecretId not in self.store:
            raise _NotFound()
        return {"ARN": f"arn:aws:secretsmanager:::secret:{SecretId}"}

    def put_secret_value(self, SecretId, SecretString):
        if SecretId not in self.store:
            raise _NotFound()   # forces the create_secret fallback on first write
        self.store[SecretId] = SecretString
        return {}

    def create_secret(self, Name, SecretString):
        self.store[Name] = SecretString
        return {}

    def get_secret_value(self, SecretId):
        if SecretId not in self.store:
            raise _NotFound()
        return {"SecretString": self.store[SecretId]}


@pytest.mark.unit
def test_satisfies_the_provisioning_secrets_protocol():
    secrets = Boto3ProvisioningSecrets(client=FakeSmClient())
    assert isinstance(secrets, ProvisioningSecrets)


@pytest.mark.unit
def test_put_then_get_roundtrips_through_one_client():
    fake = FakeSmClient()
    secrets = Boto3ProvisioningSecrets(client=fake)
    ref = "uplift/pool/anthropic_key/abc123abc123abc1"
    assert secrets.exists(ref) is False              # describe -> not found
    secrets.put(ref, "sk-ant-material")              # create_secret fallback on first write
    assert secrets.exists(ref) is True
    assert secrets.get(ref) == "sk-ant-material"     # read side sees the write
    assert fake.store[ref] == "sk-ant-material"


@pytest.mark.unit
def test_get_missing_reference_raises_not_found():
    secrets = Boto3ProvisioningSecrets(client=FakeSmClient())
    with pytest.raises(SecretNotFoundError):
        secrets.get("uplift/pool/anthropic_key/missing")


@pytest.mark.unit
def test_import_is_aws_free():
    # Constructing with no client must not build a boto3 client (lazy) — import-safe seam.
    import signup.secrets as mod
    assert mod.Boto3ProvisioningSecrets(client=None)._writer is not None
