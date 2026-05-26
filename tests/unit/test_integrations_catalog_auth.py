from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from tracecat.db.models import Integration, OAuthIntegration, Secret
from tracecat.integrations.catalog.schemas import (
    CatalogAuthOption,
    CatalogCredentialField,
    CatalogStaticKVConnectionCreate,
)
from tracecat.integrations.catalog.service import (
    ConnectionsService,
    IntegrationCatalogService,
    _record_secret_fields,
    _validate_static_kv_fields,
)
from tracecat.integrations.enums import (
    ConnectionAuthMethod,
    IntegrationSource,
    OAuthGrantType,
)
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT
from tracecat.secrets.schemas import SecretKeyValue
from tracecat.secrets.service import SecretsService


def test_record_secret_fields_uses_declared_registry_keys() -> None:
    fields_by_name: dict[str, list[CatalogCredentialField]] = {}

    _record_secret_fields(
        fields_by_name,
        {
            "name": "abuseipdb",
            "keys": ["ABUSEIPDB_API_KEY"],
            "optional_keys": ["ABUSEIPDB_BASE_URL"],
        },
    )

    fields = fields_by_name["abuseipdb"]
    assert [(field.key, field.required) for field in fields] == [
        ("ABUSEIPDB_API_KEY", True),
        ("ABUSEIPDB_BASE_URL", False),
    ]
    assert [field.label for field in fields] == [
        "Abuseipdb API key",
        "Abuseipdb base URL",
    ]


def test_record_secret_fields_skips_oauth_secrets() -> None:
    fields_by_name: dict[str, list[CatalogCredentialField]] = {}

    _record_secret_fields(
        fields_by_name,
        {
            "type": "oauth",
            "provider_id": "slack",
            "grant_type": "authorization_code",
        },
    )

    assert fields_by_name == {}


def test_record_secret_fields_promotes_duplicate_optional_key_to_required() -> None:
    fields_by_name: dict[str, list[CatalogCredentialField]] = {}

    _record_secret_fields(
        fields_by_name,
        {"name": "jira", "optional_keys": ["JIRA_API_TOKEN"]},
    )
    _record_secret_fields(
        fields_by_name,
        {"name": "jira", "keys": ["JIRA_API_TOKEN"]},
    )

    [field] = fields_by_name["jira"]
    assert field.key == "JIRA_API_TOKEN"
    assert field.required is True


def test_validate_static_kv_fields_rejects_missing_and_unknown_keys() -> None:
    option = CatalogAuthOption(
        auth_method=ConnectionAuthMethod.STATIC_KV,
        label="API key",
        fields=fields_by_name_from_secret(
            {"name": "abuseipdb", "keys": ["ABUSEIPDB_API_KEY"]}
        ),
    )

    with pytest.raises(ValueError, match="Missing required credential fields"):
        _validate_static_kv_fields(option, {})

    with pytest.raises(ValueError, match="Unsupported credential fields"):
        _validate_static_kv_fields(
            option,
            {"ABUSEIPDB_API_KEY": "secret", "API_KEY": "wrong"},
        )


@pytest.mark.anyio
async def test_workspace_integration_does_not_inherit_platform_provider_options(
    session, svc_role
) -> None:
    service = IntegrationCatalogService(session, role=svc_role)
    service._static_fields_for_secret = cast(Any, AsyncMock(return_value=[]))
    integration = Integration(
        workspace_id=svc_role.workspace_id,
        namespace="github",
        display_name="GitHub",
        source=IntegrationSource.WORKSPACE,
    )

    assert await service.auth_options_for(integration) == []


@pytest.mark.anyio
async def test_platform_integration_exposes_provider_options(session, svc_role) -> None:
    service = IntegrationCatalogService(session, role=svc_role)
    service._static_fields_for_secret = cast(Any, AsyncMock(return_value=[]))
    integration = Integration(
        workspace_id=None,
        namespace="github",
        display_name="GitHub",
        source=IntegrationSource.PLATFORM,
    )

    options = await service.auth_options_for(integration)

    assert [(option.auth_method, option.provider_id) for option in options] == [
        (ConnectionAuthMethod.OAUTH_AUTH_CODE, "github")
    ]


@pytest.mark.anyio
async def test_static_secrets_are_projected_as_connection_summaries(
    session, svc_role
) -> None:
    integration = Integration(
        workspace_id=None,
        namespace="virustotal",
        display_name="VirusTotal",
        source=IntegrationSource.PLATFORM,
    )
    secret = Secret(
        workspace_id=svc_role.workspace_id,
        name="virustotal",
        type="custom",
        encrypted_keys=SecretsService(session, role=svc_role).encrypt_keys(
            [SecretKeyValue(key="VIRUSTOTAL_API_KEY", value=SecretStr("secret"))]
        ),
        environment=DEFAULT_SECRETS_ENVIRONMENT,
    )
    session.add_all([integration, secret])
    await session.flush()

    connections = await ConnectionsService(
        session, role=svc_role
    ).list_connection_summaries(integration)

    assert len(connections) == 1
    assert connections[0].id == secret.id
    assert connections[0].integration_id == integration.id
    assert connections[0].auth_method == ConnectionAuthMethod.STATIC_KV
    assert connections[0].metadata["source"] == "secret"


@pytest.mark.anyio
async def test_create_static_connection_writes_secret_source_of_truth(
    session, svc_role
) -> None:
    integration = Integration(
        workspace_id=None,
        namespace="abuseipdb",
        display_name="AbuseIPDB",
        source=IntegrationSource.PLATFORM,
    )
    session.add(integration)
    await session.flush()

    connection = await ConnectionsService(session, role=svc_role).create_connection(
        integration,
        CatalogStaticKVConnectionCreate(
            keys={"ABUSEIPDB_API_KEY": "test-api-key"},
        ),
    )
    await session.flush()

    secret = (
        await session.execute(
            select(Secret).where(
                Secret.workspace_id == svc_role.workspace_id,
                Secret.name == "abuseipdb",
                Secret.environment == DEFAULT_SECRETS_ENVIRONMENT,
            )
        )
    ).scalar_one()
    decrypted = {
        kv.key: kv.value.get_secret_value()
        for kv in SecretsService(session, role=svc_role).decrypt_keys(
            secret.encrypted_keys
        )
    }

    assert connection.id == secret.id
    assert connection.metadata["source"] == "secret"
    assert decrypted == {"ABUSEIPDB_API_KEY": "test-api-key"}


@pytest.mark.anyio
async def test_oauth_integrations_are_projected_as_connection_summaries(
    session, svc_role
) -> None:
    integration = Integration(
        workspace_id=None,
        namespace="github",
        display_name="GitHub",
        source=IntegrationSource.PLATFORM,
    )
    session.add(integration)
    await session.flush()

    session.add_all(
        [
            OAuthIntegration(
                workspace_id=svc_role.workspace_id,
                user_id=None,
                provider_id="github",
                grant_type=OAuthGrantType.AUTHORIZATION_CODE,
                encrypted_access_token=b"access-token",
                encrypted_client_id=b"client-id",
                encrypted_client_secret=b"client-secret",
            ),
            OAuthIntegration(
                workspace_id=svc_role.workspace_id,
                user_id=None,
                provider_id="github",
                grant_type=OAuthGrantType.AUTHORIZATION_CODE,
                encrypted_access_token=b"",
                encrypted_client_id=b"admin-client-id",
                encrypted_client_secret=b"admin-client-secret",
            ),
        ]
    )
    await session.flush()

    connections = await ConnectionsService(
        session, role=svc_role
    ).list_connection_summaries(integration)

    assert len(connections) == 1
    assert connections[0].integration_id == integration.id
    assert connections[0].auth_method == ConnectionAuthMethod.OAUTH_AUTH_CODE
    assert connections[0].metadata["source"] == "oauth_integration"


def fields_by_name_from_secret(
    secret: dict[str, object],
) -> list[CatalogCredentialField]:
    fields_by_name: dict[str, list[CatalogCredentialField]] = {}
    _record_secret_fields(fields_by_name, secret)
    name = secret["name"]
    assert isinstance(name, str)
    return fields_by_name[name]
