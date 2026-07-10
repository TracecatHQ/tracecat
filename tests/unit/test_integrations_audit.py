import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.contexts import ctx_role
from tracecat.db.models import OAuthIntegration
from tracecat.integrations.enums import MCPAuthType, OAuthGrantType
from tracecat.integrations.schemas import (
    MCPHttpIntegrationCreate,
    MCPIntegrationUpdate,
    ProviderKey,
)
from tracecat.integrations.service import IntegrationService


@pytest.fixture(autouse=True, scope="session")
def default_org() -> None:
    pass


@pytest.fixture(autouse=True, scope="session")
def workflow_bucket() -> None:
    pass


@pytest.fixture(autouse=True)
def clean_redis_db() -> None:
    pass


@pytest.fixture
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config,
        "TRACECAT__DB_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )


@pytest.fixture
def audit_role() -> Role:
    return Role(
        type="user",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=ADMIN_SCOPES,
    )


@pytest.fixture
def integration_service(
    audit_role: Role,
    encryption_key: None,
) -> IntegrationService:
    session = AsyncMock(spec=AsyncSession)

    def set_generated_id(instance: Any) -> None:
        if getattr(instance, "id", None) is None:
            instance.id = uuid.uuid4()

    session.refresh.side_effect = set_generated_id
    return IntegrationService(session=session, role=audit_role)


@contextmanager
def capture_audit_events(role: Role) -> Iterator[list[dict[str, object]]]:
    create_event_calls: list[dict[str, object]] = []

    async def mock_create_event(*args: object, **kwargs: object) -> None:
        create_event_calls.append(kwargs)

    token = ctx_role.set(role)
    try:
        with patch.object(AuditService, "create_event", side_effect=mock_create_event):
            yield create_event_calls
    finally:
        ctx_role.reset(token)


@pytest.mark.anyio
async def test_store_integration_audits_connect_metadata_only(
    integration_service: IntegrationService,
    audit_role: Role,
) -> None:
    provider_key = ProviderKey(
        id="audit_provider",
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
    )
    access_token = "synthetic-access-token"
    refresh_token = "synthetic-refresh-token"

    with (
        patch.object(
            integration_service,
            "get_integration",
            AsyncMock(return_value=None),
        ),
        patch.object(
            integration_service,
            "_auto_create_mcp_integration_if_needed",
            AsyncMock(),
        ),
        capture_audit_events(audit_role) as events,
    ):
        await integration_service.store_integration(
            provider_key=provider_key,
            access_token=SecretStr(access_token),
            refresh_token=SecretStr(refresh_token),
        )

    assert len(events) == 1
    assert events[0]["resource_type"] == "integration"
    assert events[0]["action"] == "connect"
    assert events[0]["resource_id"] == str(provider_key)
    assert events[0]["status"] == AuditEventStatus.SUCCESS
    assert events[0]["data"] == {
        "provider_id": provider_key.id,
        "grant_type": provider_key.grant_type,
    }
    assert access_token not in str(events[0])
    assert refresh_token not in str(events[0])


@pytest.mark.anyio
async def test_create_mcp_integration_audits_create_metadata_only(
    integration_service: IntegrationService,
    audit_role: Role,
) -> None:
    credential = "synthetic-mcp-credential"
    params = MCPHttpIntegrationCreate(
        name="Audited MCP",
        server_uri="https://mcp.example.test/server",
        auth_type=MCPAuthType.CUSTOM,
        custom_credentials=SecretStr(credential),
    )

    with (
        patch.object(
            integration_service,
            "_resolve_create_platform_mcp_catalog",
            AsyncMock(return_value=None),
        ),
        patch.object(
            integration_service,
            "_generate_mcp_integration_slug",
            AsyncMock(return_value="audited-mcp"),
        ),
        capture_audit_events(audit_role) as events,
    ):
        integration = await integration_service.create_mcp_integration(params=params)

    assert len(events) == 1
    assert events[0]["resource_type"] == "mcp_integration"
    assert events[0]["action"] == "create"
    assert events[0]["resource_id"] == str(integration.id)
    assert events[0]["status"] == AuditEventStatus.SUCCESS
    assert events[0]["data"] == {
        "name": params.name,
        "slug": integration.slug,
    }
    assert credential not in str(events[0])


@pytest.mark.anyio
async def test_disconnect_and_remove_integration_audit_resource_id(
    integration_service: IntegrationService,
    audit_role: Role,
) -> None:
    assert audit_role.workspace_id is not None
    integration_id = uuid.uuid4()
    integration = OAuthIntegration(
        id=integration_id,
        workspace_id=audit_role.workspace_id,
        provider_id="audit_provider",
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        encrypted_access_token=b"encrypted",
    )

    with (
        patch.object(
            integration_service,
            "_is_mcp_lifecycle_owned_oauth_integration",
            AsyncMock(return_value=False),
        ),
        capture_audit_events(audit_role) as events,
    ):
        await integration_service.disconnect_integration(integration=integration)
        await integration_service.remove_integration(integration=integration)

    assert [event["action"] for event in events] == ["disconnect", "delete"]
    assert [event["resource_id"] for event in events] == [
        str(integration_id),
        str(integration_id),
    ]
    assert all(event["status"] == AuditEventStatus.SUCCESS for event in events)


@pytest.mark.anyio
async def test_provider_delete_audits_failure_and_distinct_targets(
    integration_service: IntegrationService,
    audit_role: Role,
) -> None:
    provider_key = ProviderKey(
        id="custom_audit_provider",
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
    )

    with (
        patch.object(
            integration_service,
            "get_custom_provider",
            AsyncMock(return_value=None),
        ),
        patch.object(
            integration_service,
            "get_integration",
            AsyncMock(return_value=None),
        ),
        capture_audit_events(audit_role) as events,
    ):
        custom_provider_deleted = await integration_service.delete_custom_provider(
            provider_key=provider_key
        )
        provider_config_removed = await integration_service.remove_provider_config(
            provider_key=provider_key
        )

    assert custom_provider_deleted is False
    assert provider_config_removed is False
    assert [event["status"] for event in events] == [
        AuditEventStatus.FAILURE,
        AuditEventStatus.FAILURE,
    ]
    assert [event["data"] for event in events] == [
        {
            "provider_id": provider_key.id,
            "grant_type": provider_key.grant_type,
            "target": "custom_provider",
        },
        {
            "provider_id": provider_key.id,
            "grant_type": provider_key.grant_type,
            "target": "provider_config",
        },
    ]


@pytest.mark.anyio
async def test_update_mcp_integration_not_found_audits_failure(
    integration_service: IntegrationService,
    audit_role: Role,
) -> None:
    mcp_integration_id = uuid.uuid4()

    with (
        patch.object(
            integration_service,
            "get_mcp_integration",
            AsyncMock(return_value=None),
        ),
        capture_audit_events(audit_role) as events,
    ):
        result = await integration_service.update_mcp_integration(
            mcp_integration_id=mcp_integration_id,
            params=MCPIntegrationUpdate(name="Updated MCP"),
        )

    assert result is None
    assert len(events) == 1
    assert events[0]["action"] == "update"
    assert events[0]["resource_id"] == str(mcp_integration_id)
    assert events[0]["status"] == AuditEventStatus.FAILURE
