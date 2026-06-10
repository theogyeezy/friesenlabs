"""Unit: per-tenant connector credentials (TODO INT/P1).

  * tenant_secret_ref formats the uplift/{tenant_id}/{source} convention
  * Boto3SecretProvider resolves SecretString/SecretBinary via an injected fake
    Secrets Manager client; a missing secret maps to SecretNotFoundError while
    other client errors propagate untouched
  * HubSpotConnector.authenticate resolves the PER-TENANT ref first and only
    falls back to the DEPRECATED shared ref (with a DeprecationWarning)
  * the resolved token is handed to the source client via set_token (the real
    HubSpotRestClient path)
"""
import warnings

import pytest

from ingest.connectors.base import (
    Boto3SecretProvider,
    SecretNotFoundError,
    tenant_secret_ref,
)
from ingest.connectors.hubspot import HUBSPOT_TOKEN_SECRET_REF, HubSpotConnector
from ingest.pipeline import InMemoryRawSink, InMemoryStructuredSink

TENANT = "11111111-1111-1111-1111-111111111111"
PER_TENANT_REF = f"uplift/{TENANT}/hubspot"


# --------------------------------------------------------------------------- naming
@pytest.mark.unit
def test_tenant_secret_ref_convention():
    assert tenant_secret_ref(TENANT, "hubspot") == PER_TENANT_REF
    assert tenant_secret_ref("t2", "stripe") == "uplift/t2/stripe"


# --------------------------------------------------------------------------- provider
class ResourceNotFoundException(Exception):
    """Named like the AWS error code — the class-name detection path."""


class _ClientErrorLike(Exception):
    """Shaped like botocore ClientError — the .response detection path."""

    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeSecretsManagerClient:
    def __init__(self, secrets=None, error=None):
        self.secrets = secrets or {}
        self.error = error
        self.calls = []

    def get_secret_value(self, *, SecretId):
        self.calls.append(SecretId)
        if self.error is not None:
            raise self.error
        if SecretId not in self.secrets:
            raise ResourceNotFoundException(SecretId)
        return self.secrets[SecretId]


@pytest.mark.unit
def test_provider_returns_secret_string():
    client = FakeSecretsManagerClient({PER_TENANT_REF: {"SecretString": "pat-tok"}})
    provider = Boto3SecretProvider(client=client)
    assert provider.get_secret(PER_TENANT_REF) == "pat-tok"
    assert client.calls == [PER_TENANT_REF]


@pytest.mark.unit
def test_provider_decodes_secret_binary():
    client = FakeSecretsManagerClient({"ref": {"SecretBinary": b"raw-bytes"}})
    assert Boto3SecretProvider(client=client).get_secret("ref") == "raw-bytes"


@pytest.mark.unit
def test_provider_missing_secret_maps_to_not_found_by_class_name():
    provider = Boto3SecretProvider(client=FakeSecretsManagerClient({}))
    with pytest.raises(SecretNotFoundError):
        provider.get_secret("uplift/nope/hubspot")


@pytest.mark.unit
def test_provider_missing_secret_maps_to_not_found_by_response_code():
    client = FakeSecretsManagerClient(error=_ClientErrorLike("ResourceNotFoundException"))
    with pytest.raises(SecretNotFoundError):
        Boto3SecretProvider(client=client).get_secret("ref")


@pytest.mark.unit
def test_provider_other_errors_propagate_untouched():
    client = FakeSecretsManagerClient(error=_ClientErrorLike("AccessDeniedException"))
    with pytest.raises(_ClientErrorLike):
        Boto3SecretProvider(client=client).get_secret("ref")


# --------------------------------------------------------------------------- connector resolution
class RecordingSecrets:
    """A SecretProvider fake over a dict; records every ref asked for."""

    def __init__(self, secrets):
        self.secrets = secrets
        self.calls = []

    def get_secret(self, ref):
        self.calls.append(ref)
        if ref not in self.secrets:
            raise SecretNotFoundError(ref)
        return self.secrets[ref]


class TokenRecordingClient:
    """Source-client fake exposing set_token (like HubSpotRestClient)."""

    def __init__(self):
        self.token = None

    def set_token(self, token):
        self.token = token

    def list_companies(self, since):
        return []

    def list_contacts(self, since):
        return []

    def list_deals(self, since):
        return []

    def list_notes(self, since):
        return []


def _connector(secrets, client=None):
    return HubSpotConnector(
        TENANT,
        client=client if client is not None else TokenRecordingClient(),
        secrets=secrets,
        raw_sink=InMemoryRawSink(),
        structured_sink=InMemoryStructuredSink(),
    )


@pytest.mark.unit
def test_authenticate_prefers_per_tenant_secret_no_warning():
    secrets = RecordingSecrets({PER_TENANT_REF: "pat-per-tenant"})
    client = TokenRecordingClient()
    conn = _connector(secrets, client)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        conn.authenticate()
    assert secrets.calls == [PER_TENANT_REF]          # never touched the shared ref
    assert client.token == "pat-per-tenant"           # handed to the source client
    assert not [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert conn._authed


@pytest.mark.unit
def test_authenticate_falls_back_to_shared_ref_with_deprecation_warning():
    secrets = RecordingSecrets({HUBSPOT_TOKEN_SECRET_REF: "pat-shared"})
    client = TokenRecordingClient()
    conn = _connector(secrets, client)
    with pytest.warns(DeprecationWarning, match="DEPRECATED"):
        conn.authenticate()
    assert secrets.calls == [PER_TENANT_REF, HUBSPOT_TOKEN_SECRET_REF]  # per-tenant FIRST
    assert client.token == "pat-shared"
    assert conn._authed


@pytest.mark.unit
def test_authenticate_empty_token_everywhere_raises():
    secrets = RecordingSecrets({HUBSPOT_TOKEN_SECRET_REF: ""})
    with pytest.warns(DeprecationWarning):
        with pytest.raises(RuntimeError, match="empty token"):
            _connector(secrets).authenticate()


@pytest.mark.unit
def test_authenticate_tolerates_clients_without_set_token():
    class PlainClient:
        def list_companies(self, since):
            return []

        def list_contacts(self, since):
            return []

        def list_deals(self, since):
            return []

        def list_notes(self, since):
            return []

    secrets = RecordingSecrets({PER_TENANT_REF: "tok"})
    conn = _connector(secrets, PlainClient())
    conn.authenticate()  # must not blow up on the missing set_token
    assert conn._authed
