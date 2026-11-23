"""Azure Key Vault provider, with the SDK's credential and client replaced by fakes."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceNotFoundError,
    ServiceRequestError,
)
from azure.identity import AzureAuthorityHosts
from pydantic import SecretStr, ValidationError
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.secrets.errors import SecretResolutionError
from tracecat_ee.secrets.providers import azure_key_vault as akv
from tracecat_ee.secrets.references.service import SecretReferencesService
from tracecat_ee.secrets.resolver import (
    check_external_reference,
    resolve_external_references,
)
from tracecat_ee.secrets.stores.backends import get_backend, parse_store_config
from tracecat_ee.secrets.stores.service import SecretStoresService

from tracecat.auth.secrets import get_db_encryption_key
from tracecat.auth.types import Role
from tracecat.db.models import Secret, Workspace
from tracecat.exceptions import TracecatConflictError
from tracecat.secrets.encryption import encrypt_value
from tracecat.secrets.enums import (
    SecretMappingMode,
    SecretResolutionErrorCode,
    SecretSource,
    SecretStoreProvider,
)
from tracecat.secrets.schemas import (
    AzureKeyVaultStoreConfig,
    AzureKeyVaultStoreCreate,
    AzureKeyVaultStoreUpdate,
    SecretJsonField,
    SecretKeyMapping,
    SecretReferenceCreate,
    SecretStoreCreate,
    SecretStoreUpdate,
    parse_secret_store_config,
)
from tracecat.secrets.types import ExternalSecretReference, Failure, StoreContext

pytestmark = pytest.mark.anyio

AZURE = SecretStoreProvider.AZURE_KEY_VAULT
STORE_ID = uuid.uuid4()
TENANT_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
CLIENT_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
CLIENT_SECRET = "planted~client~secret~value"
VAULT_URL = "https://example-vault.vault.azure.net"
CONFIG = AzureKeyVaultStoreConfig(
    vault_url=VAULT_URL, tenant_id=TENANT_ID, client_id=CLIENT_ID
)
VALUES = {
    "api-token": "value-a",
    "db-creds": '{"username": "user-c", "password": "pass-c"}',
}


def http_error(
    cls: type[HttpResponseError], status: int, code: str | None = None
) -> HttpResponseError:
    """An SDK error as Key Vault raises it, with a message that must not leak."""
    error = cls(message=f"planted vendor text {CLIENT_SECRET}")
    error.status_code = status
    error.error = SimpleNamespace(code=code) if code else None  # pyright: ignore[reportAttributeAccessIssue]
    return error


class FakeVault:
    """Records credentials and reads; serves ``VALUES`` or planted errors."""

    def __init__(self, errors: dict[str, Exception] | None = None) -> None:
        self.errors = errors or {}
        self.credentials: list[dict[str, Any]] = []
        self.clients: list[dict[str, Any]] = []
        self.reads: list[str] = []
        self.closed: list[str] = []
        self.delay = 0.0

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeVault:
        vault = self

        class Credential:
            def __init__(self, **kwargs: Any) -> None:
                vault.credentials.append(kwargs)

            async def __aenter__(self) -> Credential:
                return self

            async def __aexit__(self, *exc: object) -> None:
                vault.closed.append("credential")

        class Client:
            def __init__(self, **kwargs: Any) -> None:
                vault.clients.append(kwargs)

            async def __aenter__(self) -> Client:
                return self

            async def __aexit__(self, *exc: object) -> None:
                vault.closed.append("client")

            async def get_secret(self, name: str) -> Any:
                vault.reads.append(name)
                if vault.delay:
                    await asyncio.sleep(vault.delay)
                if error := vault.errors.get(name):
                    raise error
                if name not in VALUES:
                    raise http_error(ResourceNotFoundError, 404, "SecretNotFound")
                return SimpleNamespace(value=VALUES[name])

        monkeypatch.setattr(akv, "ClientSecretCredential", Credential)
        monkeypatch.setattr(akv, "SecretClient", Client)
        return self


def _reference(
    alias: str,
    key: str,
    mapping: SecretKeyMapping,
    config: AzureKeyVaultStoreConfig = CONFIG,
) -> ExternalSecretReference:
    return ExternalSecretReference(
        secret_id=uuid.uuid4(),
        alias=alias,
        environment="default",
        store_id=STORE_ID,
        provider=AZURE,
        store_enabled=True,
        store_config=config,
        key=key,
        mapping=mapping,
    )


def sealed(
    references: Sequence[ExternalSecretReference],
) -> list[ExternalSecretReference]:
    """The references with their store's client secret sealed on them, as loaded."""
    token = encrypt_value(CLIENT_SECRET.encode(), key=get_db_encryption_key())
    return [
        dataclasses.replace(reference, encrypted_access_token=token)
        for reference in references
    ]


def _whole(key: str) -> SecretKeyMapping:
    return SecretKeyMapping(mode=SecretMappingMode.WHOLE_STRING, keys=[key])


REFERENCES = [
    _reference("alpha", "api-token", _whole("TOKEN")),
    _reference(
        "gamma",
        "db-creds",
        SecretKeyMapping(
            mode=SecretMappingMode.JSON,
            fields=[
                SecretJsonField(key="USER", field="username"),
                SecretJsonField(key="PASS", field="password"),
            ],
        ),
    ),
]


async def test_reads_map_onto_declared_keys_with_one_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = FakeVault().install(monkeypatch)
    resolved = await resolve_external_references(sealed(REFERENCES))

    assert resolved == {
        "alpha": {"TOKEN": "value-a"},
        "gamma": {"USER": "user-c", "PASS": "pass-c"},
    }
    assert vault.credentials == [
        {
            "tenant_id": str(TENANT_ID),
            "client_id": str(CLIENT_ID),
            "client_secret": CLIENT_SECRET,
            "authority": AzureAuthorityHosts.AZURE_PUBLIC_CLOUD,
        }
    ]
    assert len(vault.clients) == 1
    assert vault.clients[0]["vault_url"] == VAULT_URL
    assert vault.clients[0]["verify_challenge_resource"] is True
    assert sorted(vault.reads) == ["api-token", "db-creds"]
    assert sorted(vault.closed) == ["client", "credential"]


@pytest.mark.parametrize(
    ("vault_url", "authority"),
    [
        (
            "https://gov-vault.vault.usgovcloudapi.net",
            AzureAuthorityHosts.AZURE_GOVERNMENT,
        ),
        ("https://cn-vault.vault.azure.cn", AzureAuthorityHosts.AZURE_CHINA),
    ],
)
async def test_sovereign_clouds_sign_in_at_their_own_authority(
    monkeypatch: pytest.MonkeyPatch, vault_url: str, authority: str
) -> None:
    vault = FakeVault().install(monkeypatch)
    config = CONFIG.model_copy(update={"vault_url": vault_url})
    await resolve_external_references(
        sealed([_reference("alpha", "api-token", _whole("TOKEN"), config)])
    )
    assert vault.credentials[0]["authority"] == authority


async def test_sign_in_failure_fails_every_reference_after_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = http_error(ClientAuthenticationError, 401)
    vault = FakeVault({"api-token": error, "db-creds": error}).install(monkeypatch)
    outcomes = await akv.AzureKeyVaultBackend().fetch(
        StoreContext(
            store_id=STORE_ID,
            provider=AZURE,
            enabled=True,
            config=CONFIG,
            access_token=CLIENT_SECRET,
        ),
        ["api-token", "db-creds"],
    )
    assert outcomes == {
        "api-token": Failure(SecretResolutionErrorCode.AUTH_FAILED, "http_401"),
        "db-creds": Failure(SecretResolutionErrorCode.AUTH_FAILED, "http_401"),
    }
    assert vault.reads == ["api-token"]


async def test_per_secret_failure_still_reads_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = FakeVault().install(monkeypatch)
    outcomes = await akv.fetch_from_vault(
        CONFIG, CLIENT_SECRET, ["missing", "api-token"]
    )
    assert outcomes == {
        "missing": Failure(SecretResolutionErrorCode.NOT_FOUND, "SecretNotFound"),
        "api-token": "value-a",
    }
    assert vault.reads == ["missing", "api-token"]


async def test_unclassified_failure_on_one_secret_still_reads_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeVault({"api-token": http_error(HttpResponseError, 400)}).install(monkeypatch)
    outcomes = await akv.fetch_from_vault(
        CONFIG, CLIENT_SECRET, ["api-token", "db-creds"]
    )
    assert outcomes == {
        "api-token": Failure(SecretResolutionErrorCode.UNKNOWN, "http_400"),
        "db-creds": VALUES["db-creds"],
    }


async def test_unreachable_azure_fails_every_reference_after_one_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = FakeVault({"api-token": ServiceRequestError("planted dns text")}).install(
        monkeypatch
    )
    outcomes = await akv.fetch_from_vault(
        CONFIG, CLIENT_SECRET, ["api-token", "db-creds"]
    )
    failure = Failure(SecretResolutionErrorCode.UNAVAILABLE, "connection")
    assert outcomes == {"api-token": failure, "db-creds": failure}
    assert vault.reads == ["api-token"]


async def test_failed_run_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeVault({"api-token": http_error(HttpResponseError, 403, "Forbidden")}).install(
        monkeypatch
    )
    with pytest.raises(SecretResolutionError) as info:
        await resolve_external_references(sealed(REFERENCES))

    error = info.value
    assert error.code == SecretResolutionErrorCode.ACCESS_DENIED
    assert error.vendor_code == "Forbidden"
    assert "Azure Key Vault" in str(error)
    assert error.__cause__ is None
    assert error.__suppress_context__ or error.__context__ is None
    assert CLIENT_SECRET not in str(error)
    assert "planted vendor text" not in str(error)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            http_error(ClientAuthenticationError, 401),
            Failure(SecretResolutionErrorCode.AUTH_FAILED, "http_401"),
        ),
        (
            http_error(ResourceNotFoundError, 404, "SecretNotFound"),
            Failure(SecretResolutionErrorCode.NOT_FOUND, "SecretNotFound"),
        ),
        (
            http_error(HttpResponseError, 403, "Forbidden"),
            Failure(SecretResolutionErrorCode.ACCESS_DENIED, "Forbidden"),
        ),
        (
            http_error(HttpResponseError, 429, "Throttled"),
            Failure(SecretResolutionErrorCode.THROTTLED, "Throttled"),
        ),
        (
            http_error(HttpResponseError, 503),
            Failure(SecretResolutionErrorCode.UNAVAILABLE, "http_503"),
        ),
        (
            http_error(HttpResponseError, 400, "free text with spaces"),
            Failure(SecretResolutionErrorCode.UNKNOWN, "http_400"),
        ),
        (
            ServiceRequestError("planted dns text"),
            Failure(SecretResolutionErrorCode.UNAVAILABLE, "connection"),
        ),
        (RuntimeError("planted"), Failure(SecretResolutionErrorCode.UNKNOWN, None)),
    ],
)
def test_sdk_errors_map_by_type_and_status(error: Exception, expected: Failure) -> None:
    assert akv.classify_error(error) == expected


async def test_deadline_fails_every_reference_as_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = FakeVault().install(monkeypatch)
    vault.delay = 1.0
    monkeypatch.setattr(akv, "FETCH_TIMEOUT_SECONDS", 0.05)
    outcomes = await akv.fetch_from_vault(CONFIG, CLIENT_SECRET, ["api-token"])
    assert outcomes == {
        "api-token": Failure(SecretResolutionErrorCode.TIMEOUT, "deadline")
    }
    assert sorted(vault.closed) == ["client", "credential"]


async def test_missing_client_secret_fails_before_the_sdk_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = FakeVault().install(monkeypatch)
    outcomes = await akv.AzureKeyVaultBackend().fetch(
        StoreContext(store_id=STORE_ID, provider=AZURE, enabled=True, config=CONFIG),
        ["api-token"],
    )
    assert outcomes == {
        "api-token": Failure(SecretResolutionErrorCode.CREDENTIAL_MISSING)
    }
    assert vault.credentials == []


async def test_check_returns_key_names_only(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeVault().install(monkeypatch)
    result = await check_external_reference(sealed(REFERENCES)[1])
    assert result.ok is True
    assert result.resolved_keys == ["PASS", "USER"]


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("https://Example-Vault.vault.azure.net/", VAULT_URL),
        ("https://abc.vault.usgovcloudapi.net", "https://abc.vault.usgovcloudapi.net"),
        ("https://abc.vault.azure.cn:443", "https://abc.vault.azure.cn"),
    ],
)
def test_vault_url_is_stored_normalized(raw: str, stored: str) -> None:
    config = AzureKeyVaultStoreConfig(
        vault_url=raw, tenant_id=TENANT_ID, client_id=CLIENT_ID
    )
    assert config.vault_url == stored


@pytest.mark.parametrize(
    "raw",
    [
        "http://example-vault.vault.azure.net",
        "https://example-vault.vault.azure.net:8443",
        "https://example-vault.vault.azure.net/secrets/x",
        "https://example-vault.vault.azure.net?x=1",
        "https://user@example-vault.vault.azure.net",
        "https://example-vault.vault.azure.net.attacker.test",
        "https://attacker.test/example-vault.vault.azure.net",
        "https://a.b.vault.azure.net",
        "https://ab.vault.azure.net",
        "https://1abc.vault.azure.net",
        "https://ab--c.vault.azure.net",
        "https://abc-.vault.azure.net",
        "https://" + "a" * 25 + ".vault.azure.net",
        "https://example-vault.managedhsm.azure.net",
        "https://example-vault.vault.azure.net\\@attacker.test",
        "example-vault",
    ],
)
def test_only_key_vault_hosts_are_accepted(raw: str) -> None:
    with pytest.raises(ValidationError):
        AzureKeyVaultStoreConfig(
            vault_url=raw, tenant_id=TENANT_ID, client_id=CLIENT_ID
        )


def test_tenant_and_client_must_be_ids() -> None:
    with pytest.raises(ValidationError):
        AzureKeyVaultStoreConfig(
            vault_url=VAULT_URL,
            tenant_id="contoso.onmicrosoft.com",  # pyright: ignore[reportArgumentType]
            client_id=CLIENT_ID,
        )


@pytest.mark.parametrize("name", ["api-token", "A1", "x" * 127])
def test_secret_names_are_valid_references(name: str) -> None:
    akv.AzureKeyVaultBackend().validate_reference(CONFIG, name)


@pytest.mark.parametrize(
    "name", ["", "x" * 128, "has_underscore", "has space", "a/b", "name/version"]
)
def test_invalid_secret_names_are_refused(name: str) -> None:
    with pytest.raises(ValueError, match="secret's name"):
        akv.AzureKeyVaultBackend().validate_reference(CONFIG, name)


def test_vault_cannot_move_while_referenced() -> None:
    backend = akv.AzureKeyVaultBackend()
    moved = CONFIG.model_copy(update={"vault_url": "https://other.vault.azure.net"})
    with pytest.raises(TracecatConflictError):
        backend.check_change(CONFIG, moved, references=["api-token"])
    backend.check_change(CONFIG, moved, references=[])
    # A new tenant or client still reads the same vault.
    backend.check_change(
        CONFIG,
        CONFIG.model_copy(
            update={"tenant_id": uuid.uuid4(), "client_id": uuid.uuid4()}
        ),
        references=["api-token"],
    )


def test_update_keeps_unset_fields() -> None:
    new_client = uuid.uuid4()
    updated = akv.AzureKeyVaultBackend().update_config(
        CONFIG, AzureKeyVaultStoreUpdate(provider=AZURE, client_id=new_client)
    )
    assert updated == CONFIG.model_copy(update={"client_id": new_client})


def test_persisted_config_round_trips() -> None:
    assert get_backend(AZURE).provider == AZURE
    assert parse_secret_store_config(CONFIG.model_dump(mode="json")) == CONFIG
    with pytest.raises(ValueError, match="not bitwarden"):
        parse_secret_store_config(
            CONFIG.model_dump(mode="json"),
            provider=SecretStoreProvider.BITWARDEN_SECRETS_MANAGER,
        )


def _create(name: str, secret: str | None = CLIENT_SECRET) -> SecretStoreCreate:
    return SecretStoreCreate(
        name=name,
        provider=AZURE,
        config=AzureKeyVaultStoreCreate(
            provider=AZURE,
            vault_url=VAULT_URL,
            tenant_id=TENANT_ID,
            client_id=CLIENT_ID,
        ),
        access_token=SecretStr(secret) if secret is not None else None,
    )


@pytest.mark.usefixtures("db")
async def test_store_lifecycle_with_a_reference(
    session: AsyncSession, svc_admin_role: Role, svc_workspace: Workspace
) -> None:
    stores = SecretStoresService(session=session, role=svc_admin_role)

    # Given no client secret, Then the store cannot be created enabled
    with pytest.raises(ValueError, match="Set an access token"):
        await stores.create_store(_create("azure-none", None))

    # Given a store with a client secret and one reference
    store = await stores.create_store(_create("azure"))
    assert store.encrypted_access_token is not None
    assert CLIENT_SECRET.encode() not in store.encrypted_access_token
    await stores.authorize_workspace(store, svc_workspace.id)
    references = SecretReferencesService(session=session, role=svc_admin_role)
    with pytest.raises(ValueError, match="secret's name"):
        await references.create_secret_reference(
            SecretReferenceCreate(
                name="akv_bad",
                store_id=store.id,
                remote_reference="not_a_name",
                key_mapping=_whole("TOKEN"),
            )
        )
    created = await references.create_secret_reference(
        SecretReferenceCreate(
            name="akv_ref",
            store_id=store.id,
            remote_reference="api-token",
            key_mapping=_whole("TOKEN"),
        )
    )
    assert created.source == SecretSource.AZURE_KEY_VAULT
    created_id = created.id

    # When the vault moves, Then it is a conflict
    with pytest.raises(TracecatConflictError):
        await stores.update_store(
            store,
            SecretStoreUpdate(
                config=AzureKeyVaultStoreUpdate(
                    provider=AZURE, vault_url="https://other.vault.azure.net"
                )
            ),
        )
    await session.rollback()
    await session.refresh(store)

    # When the client and secret rotate, Then it is accepted
    new_client = uuid.uuid4()
    await stores.update_store(
        store,
        SecretStoreUpdate(
            config=AzureKeyVaultStoreUpdate(provider=AZURE, client_id=new_client),
            access_token=SecretStr("rotated~secret"),
        ),
    )
    config = parse_store_config(store)
    assert isinstance(config, AzureKeyVaultStoreConfig)
    assert config.client_id == new_client
    assert config.vault_url == VAULT_URL

    # Given no references, When the vault moves, Then it is accepted
    await session.execute(delete(Secret).where(Secret.id == created_id))
    await session.commit()
    await stores.update_store(
        store,
        SecretStoreUpdate(
            config=AzureKeyVaultStoreUpdate(
                provider=AZURE, vault_url="https://other.vault.azure.net"
            )
        ),
    )
    moved = parse_store_config(store)
    assert isinstance(moved, AzureKeyVaultStoreConfig)
    assert moved.vault_url == "https://other.vault.azure.net"
