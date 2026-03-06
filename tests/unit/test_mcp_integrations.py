"""Test suite for MCP integrations.

This test suite covers MCP integration functionality including:
- CRUD operations for all auth types (OAuth2, Custom, None)
- Authentication type switching and credential swapping
- Workspace isolation
- Validation and edge cases
- MCP provider OAuth discovery behavior
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr, TypeAdapter
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.agent.mcp import user_client as mcp_user_client
from tracecat.agent.mcp.catalog import MCPServerCatalog
from tracecat.agent.mcp.local_runtime.types import (
    LocalMCPDiscoveryError,
    LocalMCPDiscoveryPhase,
)
from tracecat.agent.preset.service import AgentPresetService
from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.db.models import (
    AgentPreset,
    MCPIntegration,
    MCPIntegrationCatalogEntry,
    MCPIntegrationDiscoveryAttempt,
    OAuthIntegration,
)
from tracecat.integrations.enums import (
    MCPAuthType,
    MCPCatalogArtifactType,
    MCPDiscoveryAttemptStatus,
    MCPDiscoveryStatus,
    MCPDiscoveryTrigger,
    MCPTransport,
    OAuthGrantType,
)
from tracecat.integrations.mcp_discovery_types import MCPDiscoveryWorkflowArgs
from tracecat.integrations.providers.base import (
    MCPAuthProvider,
    OAuthDiscoveryResult,
)
from tracecat.integrations.providers.sentry.mcp import SentryMCPProvider
from tracecat.integrations.providers.wiz.mcp import WizMCPProvider
from tracecat.integrations.schemas import (
    MCPHttpIntegrationCreate,
    MCPIntegrationCreate,
    MCPIntegrationUpdate,
    MCPStdioIntegrationCreate,
    ProviderConfig,
    ProviderKey,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.service import IntegrationService

pytestmark = pytest.mark.usefixtures("db")


class FakeTemporalClient:
    """Minimal Temporal client stub for MCP discovery enqueue tests."""

    def __init__(self) -> None:
        self.started_workflows: list[dict[str, object]] = []

    async def start_workflow(
        self, workflow: object, args: object, **kwargs: object
    ) -> None:
        self.started_workflows.append(
            {
                "workflow": workflow,
                "args": args,
                "kwargs": kwargs,
            }
        )


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set up encryption key for integration service tests."""
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TRACECAT__DB_ENCRYPTION_KEY", key)
    return key


@pytest.fixture(autouse=True)
def temporal_client_stub(monkeypatch: pytest.MonkeyPatch) -> FakeTemporalClient:
    """Stub Temporal workflow starts for MCP discovery enqueue paths."""
    client = FakeTemporalClient()

    async def _get_temporal_client() -> FakeTemporalClient:
        return client

    monkeypatch.setattr(
        "tracecat.integrations.service.get_temporal_client",
        _get_temporal_client,
    )
    return client


@pytest.fixture
async def integration_service(
    session: AsyncSession, svc_role: Role, encryption_key: str
) -> IntegrationService:
    """Create an integration service instance for testing."""
    return IntegrationService(session=session, role=svc_role)


@pytest.fixture
async def oauth_integration(
    integration_service: IntegrationService,
) -> OAuthIntegration:
    """Create a test OAuth integration."""
    provider_key = ProviderKey(
        id="github",
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
    )
    integration = await integration_service.store_integration(
        provider_key=provider_key,
        access_token=SecretStr("test_access_token"),
        refresh_token=SecretStr("test_refresh_token"),
        expires_in=3600,
    )
    return integration


@pytest.mark.anyio
class TestMCPIntegrationCRUD:
    """Test basic CRUD operations for MCP integrations."""

    async def _insert_catalog_entry(
        self,
        *,
        integration_service: IntegrationService,
        mcp_integration_id: uuid.UUID,
        integration_name: str,
        artifact_type: MCPCatalogArtifactType,
        artifact_key: str,
        artifact_ref: str,
        is_active: bool = True,
    ) -> None:
        await integration_service.session.execute(
            insert(MCPIntegrationCatalogEntry).values(
                id=uuid.uuid4(),
                mcp_integration_id=mcp_integration_id,
                workspace_id=integration_service.workspace_id,
                integration_name=integration_name,
                artifact_type=artifact_type.value,
                artifact_key=artifact_key,
                artifact_ref=artifact_ref,
                display_name=artifact_ref,
                description=None,
                input_schema={"type": "object"},
                artifact_metadata=None,
                raw_payload={"name": artifact_ref},
                content_hash=artifact_key.ljust(64, "0"),
                is_active=is_active,
                search_vector=func.to_tsvector("simple", artifact_ref),
            )
        )

    async def test_create_mcp_integration_with_oauth2(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
        temporal_client_stub: FakeTemporalClient,
    ) -> None:
        """Test creating an MCP integration with OAuth2 authentication."""
        params = MCPHttpIntegrationCreate(
            name="Test OAuth MCP",
            description="Test description",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )

        mcp_integration = await integration_service.create_mcp_integration(
            params=params
        )

        assert mcp_integration.id is not None
        assert mcp_integration.name == "Test OAuth MCP"
        assert mcp_integration.slug == "test-oauth-mcp"
        assert mcp_integration.description == "Test description"
        assert mcp_integration.transport == MCPTransport.HTTP.value
        assert mcp_integration.server_uri == "https://api.example.com/mcp"
        assert mcp_integration.auth_type == MCPAuthType.OAUTH2
        assert mcp_integration.oauth_integration_id == oauth_integration.id
        assert mcp_integration.encrypted_headers is None
        assert len(mcp_integration.scope_namespace) == 16
        assert mcp_integration.discovery_status == MCPDiscoveryStatus.PENDING.value
        assert mcp_integration.catalog_version == 0
        assert mcp_integration.last_discovery_attempt_at is not None
        assert mcp_integration.last_discovered_at is None
        assert mcp_integration.last_discovery_error_code is None
        assert mcp_integration.last_discovery_error_summary is None
        assert mcp_integration.sandbox_allow_network is False
        assert mcp_integration.sandbox_egress_allowlist is None
        assert mcp_integration.sandbox_egress_denylist is None
        assert mcp_integration.created_at is not None
        assert mcp_integration.updated_at is not None
        assert len(temporal_client_stub.started_workflows) == 1

    async def test_create_stdio_mcp_integration_enqueues_local_discovery(
        self,
        integration_service: IntegrationService,
        temporal_client_stub: FakeTemporalClient,
    ) -> None:
        """Test creating a stdio MCP integration enqueues local discovery."""
        created = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Local MCP",
                stdio_command="uvx",
                stdio_args=["example-mcp"],
            )
        )

        assert created.server_type == "stdio"
        assert created.discovery_status == MCPDiscoveryStatus.PENDING.value
        assert created.last_discovery_attempt_at is not None
        assert len(temporal_client_stub.started_workflows) == 1
        started = cast(dict[str, object], temporal_client_stub.started_workflows[0])
        kwargs = cast(dict[str, object], started["kwargs"])
        assert started["workflow"] == "mcp_local_stdio_discovery"
        assert kwargs["task_queue"] == config.TRACECAT__MCP_QUEUE
        args = cast(MCPDiscoveryWorkflowArgs, started["args"])
        assert args.trigger == MCPDiscoveryTrigger.CREATE

    async def test_create_mcp_integration_with_oauth2_additional_headers(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test creating OAuth2 MCP integration with extra custom headers."""
        custom_headers = '{"X-Wiz-Tenant": "tenant-a"}'
        params = MCPHttpIntegrationCreate(
            name="Test OAuth MCP with headers",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
            custom_credentials=SecretStr(custom_headers),
        )

        mcp_integration = await integration_service.create_mcp_integration(
            params=params
        )

        assert mcp_integration.auth_type == MCPAuthType.OAUTH2
        assert mcp_integration.encrypted_headers is not None
        assert custom_headers.encode() not in mcp_integration.encrypted_headers

    async def test_create_mcp_integration_with_custom_auth(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test creating an MCP integration with custom authentication."""
        custom_creds = '{"Authorization": "Bearer token123"}'
        params = MCPHttpIntegrationCreate(
            name="Test Custom MCP",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.CUSTOM,
            custom_credentials=SecretStr(custom_creds),
        )

        mcp_integration = await integration_service.create_mcp_integration(
            params=params
        )

        assert mcp_integration.name == "Test Custom MCP"
        assert mcp_integration.slug == "test-custom-mcp"
        assert mcp_integration.auth_type == MCPAuthType.CUSTOM
        assert mcp_integration.oauth_integration_id is None
        assert mcp_integration.encrypted_headers is not None
        # Verify credentials are encrypted
        assert custom_creds.encode() not in mcp_integration.encrypted_headers

    async def test_create_mcp_integration_with_no_auth(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test creating an MCP integration with no authentication."""
        params = MCPHttpIntegrationCreate(
            name="Test No Auth MCP",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.NONE,
        )

        mcp_integration = await integration_service.create_mcp_integration(
            params=params
        )

        assert mcp_integration.name == "Test No Auth MCP"
        assert mcp_integration.auth_type == MCPAuthType.NONE
        assert mcp_integration.oauth_integration_id is None
        assert mcp_integration.encrypted_headers is None

    async def test_get_mcp_integration(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test retrieving an MCP integration by ID."""
        params = MCPHttpIntegrationCreate(
            name="Test MCP",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )
        created = await integration_service.create_mcp_integration(params=params)

        retrieved = await integration_service.get_mcp_integration(
            mcp_integration_id=created.id
        )

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.name == created.name
        assert retrieved.server_uri == created.server_uri

    async def test_resolve_oauth2_mcp_with_additional_headers(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test OAUTH2 MCP includes additional headers without overriding Authorization."""
        params = MCPHttpIntegrationCreate(
            name="Wiz MCP",
            server_uri="https://mcp.app.wiz.io/",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
            custom_credentials=SecretStr(
                '{"Authorization":"Bearer bad-token","X-Wiz-Tenant":"tenant-a"}'
            ),
        )
        created = await integration_service.create_mcp_integration(params=params)

        preset_service = AgentPresetService(
            session=integration_service.session,
            role=integration_service.role,
        )
        resolved = await preset_service._resolve_mcp_integrations([str(created.id)])

        assert resolved is not None
        assert len(resolved) == 1
        http_config = resolved[0]
        assert http_config.get("type") == "http"
        headers = http_config.get("headers")
        assert isinstance(headers, dict)
        assert headers.get("Authorization") == "Bearer test_access_token"
        assert headers.get("X-Wiz-Tenant") == "tenant-a"

    async def test_resolve_oauth2_mcp_filters_authorization_header_variants(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test OAUTH2 MCP rejects Authorization header override with any casing."""
        params = MCPHttpIntegrationCreate(
            name="Wiz MCP with auth variants",
            server_uri="https://mcp.app.wiz.io/",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
            custom_credentials=SecretStr(
                '{"authorization":"Bearer bad-lower","AUTHORIZATION":"Bearer bad-upper","X-Wiz-Tenant":"tenant-a"}'
            ),
        )
        created = await integration_service.create_mcp_integration(params=params)

        preset_service = AgentPresetService(
            session=integration_service.session,
            role=integration_service.role,
        )
        resolved = await preset_service._resolve_mcp_integrations([str(created.id)])

        assert resolved is not None
        assert len(resolved) == 1
        headers = resolved[0].get("headers")
        assert isinstance(headers, dict)
        assert headers.get("Authorization") == "Bearer test_access_token"
        assert headers.get("X-Wiz-Tenant") == "tenant-a"
        assert "authorization" not in headers
        assert "AUTHORIZATION" not in headers

    async def test_resolve_oauth2_mcp_ignores_invalid_optional_headers(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test malformed optional OAuth2 headers do not disable MCP resolution."""
        params = MCPHttpIntegrationCreate(
            name="Wiz MCP invalid headers",
            server_uri="https://mcp.app.wiz.io/",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
            custom_credentials=SecretStr("not valid json"),
        )
        created = await integration_service.create_mcp_integration(params=params)

        preset_service = AgentPresetService(
            session=integration_service.session,
            role=integration_service.role,
        )
        resolved = await preset_service._resolve_mcp_integrations([str(created.id)])

        assert resolved is not None
        assert len(resolved) == 1
        headers = resolved[0].get("headers")
        assert isinstance(headers, dict)
        assert headers == {"Authorization": "Bearer test_access_token"}

    async def test_get_mcp_integration_not_found(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test retrieving a non-existent MCP integration."""
        non_existent_id = uuid.uuid4()

        result = await integration_service.get_mcp_integration(
            mcp_integration_id=non_existent_id
        )

        assert result is None

    async def test_list_mcp_integrations(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test listing all MCP integrations in a workspace."""
        # Create multiple integrations
        for idx in range(3):
            params = MCPHttpIntegrationCreate(
                name=f"Test MCP {idx}",
                server_uri=f"https://api{idx}.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
            await integration_service.create_mcp_integration(params=params)

        integrations = await integration_service.list_mcp_integrations()

        assert len(integrations) == 3
        assert all(
            integration.name.startswith("Test MCP") for integration in integrations
        )

    async def test_update_mcp_integration(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test updating an MCP integration."""
        params = MCPHttpIntegrationCreate(
            name="Test MCP",
            description="Original description",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )
        created = await integration_service.create_mcp_integration(params=params)
        original_scope_namespace = created.scope_namespace

        update_params = MCPIntegrationUpdate(
            name="Updated MCP",
            description="Updated description",
        )
        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=created.id, params=update_params
        )

        assert updated is not None
        assert updated.name == "Updated MCP"
        assert updated.description == "Updated description"
        assert updated.slug == "updated-mcp"  # Slug regenerated when name changes
        assert updated.scope_namespace == original_scope_namespace
        assert updated.server_uri == created.server_uri  # Unchanged

    async def test_create_mcp_integration_with_sse_transport(
        self,
        integration_service: IntegrationService,
        temporal_client_stub: FakeTemporalClient,
    ) -> None:
        """Test remote MCP integrations persist the requested transport."""
        created = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="SSE MCP",
                server_uri="https://api.example.com/sse",
                transport=MCPTransport.SSE,
                auth_type=MCPAuthType.NONE,
            )
        )

        assert created.transport == MCPTransport.SSE.value
        assert len(temporal_client_stub.started_workflows) == 1

    async def test_update_mcp_integration_enqueues_remote_discovery(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
        temporal_client_stub: FakeTemporalClient,
    ) -> None:
        """Test updating an HTTP MCP integration re-enqueues discovery."""
        created = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Refreshable MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )
        temporal_client_stub.started_workflows.clear()

        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=created.id,
            params=MCPIntegrationUpdate(transport=MCPTransport.SSE),
        )

        assert updated is not None
        assert updated.transport == MCPTransport.SSE.value
        assert updated.last_discovery_attempt_at is not None
        assert len(temporal_client_stub.started_workflows) == 1
        args = cast(
            MCPDiscoveryWorkflowArgs,
            temporal_client_stub.started_workflows[0]["args"],
        )
        assert args.trigger == MCPDiscoveryTrigger.UPDATE

    async def test_refresh_mcp_integration_discovery_enqueues_remote_discovery(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
        temporal_client_stub: FakeTemporalClient,
    ) -> None:
        """Test explicit refresh re-enqueues discovery for HTTP MCP integrations."""
        created = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Manual refresh MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )
        temporal_client_stub.started_workflows.clear()

        refreshed = await integration_service.refresh_mcp_integration_discovery(
            mcp_integration_id=created.id
        )

        assert refreshed is not None
        assert refreshed.discovery_status == MCPDiscoveryStatus.PENDING.value
        assert refreshed.last_discovery_attempt_at is not None
        assert len(temporal_client_stub.started_workflows) == 1
        args = cast(
            MCPDiscoveryWorkflowArgs,
            temporal_client_stub.started_workflows[0]["args"],
        )
        assert args.trigger == MCPDiscoveryTrigger.REFRESH

    async def test_refresh_stdio_mcp_integration_discovery_enqueues_local_discovery(
        self,
        integration_service: IntegrationService,
        temporal_client_stub: FakeTemporalClient,
    ) -> None:
        """Test explicit refresh re-enqueues discovery for stdio MCP integrations."""
        created = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Manual local refresh MCP",
                stdio_command="uvx",
                stdio_args=["example-mcp"],
            )
        )
        temporal_client_stub.started_workflows.clear()

        refreshed = await integration_service.refresh_mcp_integration_discovery(
            mcp_integration_id=created.id
        )

        assert refreshed is not None
        assert refreshed.discovery_status == MCPDiscoveryStatus.PENDING.value
        assert refreshed.last_discovery_attempt_at is not None
        assert len(temporal_client_stub.started_workflows) == 1
        started = cast(dict[str, object], temporal_client_stub.started_workflows[0])
        kwargs = cast(dict[str, object], started["kwargs"])
        assert started["workflow"] == "mcp_local_stdio_discovery"
        assert kwargs["task_queue"] == config.TRACECAT__MCP_QUEUE
        args = cast(MCPDiscoveryWorkflowArgs, started["args"])
        assert args.trigger == MCPDiscoveryTrigger.REFRESH

    async def test_get_mcp_catalog_counts(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test active catalog counts are grouped by integration and artifact type."""
        created = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Catalog MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )
        other = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Other Catalog MCP",
                server_uri="https://api2.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )

        await self._insert_catalog_entry(
            mcp_integration_id=created.id,
            integration_service=integration_service,
            integration_name=created.name,
            artifact_type=MCPCatalogArtifactType.TOOL,
            artifact_key="tool-alpha-1234567890",
            artifact_ref="tool.alpha",
        )
        await self._insert_catalog_entry(
            mcp_integration_id=created.id,
            integration_service=integration_service,
            integration_name=created.name,
            artifact_type=MCPCatalogArtifactType.RESOURCE,
            artifact_key="resource-alpha-1234567890",
            artifact_ref="resource://alpha",
        )
        await self._insert_catalog_entry(
            mcp_integration_id=created.id,
            integration_service=integration_service,
            integration_name=created.name,
            artifact_type=MCPCatalogArtifactType.PROMPT,
            artifact_key="prompt-inactive-1234567890",
            artifact_ref="prompt.inactive",
            is_active=False,
        )
        await self._insert_catalog_entry(
            mcp_integration_id=other.id,
            integration_service=integration_service,
            integration_name=other.name,
            artifact_type=MCPCatalogArtifactType.PROMPT,
            artifact_key="prompt-beta-1234567890",
            artifact_ref="prompt.beta",
        )
        await integration_service.session.commit()

        counts = await integration_service.get_mcp_catalog_counts(
            mcp_integration_ids=[created.id, other.id]
        )

        assert counts[created.id] == {
            MCPCatalogArtifactType.TOOL: 1,
            MCPCatalogArtifactType.RESOURCE: 1,
        }
        assert counts[other.id] == {MCPCatalogArtifactType.PROMPT: 1}

    async def test_run_remote_mcp_discovery_persists_catalog_and_deactivates_removed(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test remote discovery upserts catalog rows and deactivates removed ones."""
        created = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Catalog refresh MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.NONE,
            )
        )
        assert created.last_discovery_attempt_at is not None

        async def _discover_first(_: object) -> dict[str, object]:
            return {
                "artifacts": [
                    {
                        "artifact_type": MCPCatalogArtifactType.TOOL.value,
                        "artifact_ref": "search",
                        "display_name": "Search",
                        "description": "Search everything",
                        "input_schema": {"type": "object"},
                        "metadata": {"source": "tool"},
                        "raw_payload": {"name": "search"},
                        "content_hash": "a" * 64,
                    },
                    {
                        "artifact_type": MCPCatalogArtifactType.PROMPT.value,
                        "artifact_ref": "triage",
                        "display_name": "Triage",
                        "description": "Triage incidents",
                        "input_schema": {
                            "type": "object",
                            "properties": {"ticket": {"type": "string"}},
                        },
                        "metadata": {"source": "prompt"},
                        "raw_payload": {"name": "triage"},
                        "content_hash": "b" * 64,
                    },
                ]
            }

        monkeypatch.setattr(
            mcp_user_client,
            "discover_mcp_server_catalog",
            _discover_first,
        )
        first_started_at = created.last_discovery_attempt_at + timedelta(seconds=1)
        first_result = await integration_service.run_remote_mcp_discovery(
            mcp_integration_id=created.id,
            trigger=MCPDiscoveryTrigger.CREATE,
            started_at=first_started_at,
        )
        first_attempt_result = await integration_service.session.execute(
            select(MCPIntegrationDiscoveryAttempt).where(
                MCPIntegrationDiscoveryAttempt.mcp_integration_id == created.id
            )
        )
        first_attempt = first_attempt_result.scalars().one()

        assert first_result.status == MCPDiscoveryStatus.SUCCEEDED.value, (
            first_result.model_dump(),
            first_attempt.error_details,
        )
        assert first_result.catalog_version == 1

        async def _discover_second(_: object) -> dict[str, object]:
            return {
                "artifacts": [
                    {
                        "artifact_type": MCPCatalogArtifactType.TOOL.value,
                        "artifact_ref": "search",
                        "display_name": "Search",
                        "description": "Search everything",
                        "input_schema": {"type": "object"},
                        "metadata": {"source": "tool"},
                        "raw_payload": {"name": "search"},
                        "content_hash": "c" * 64,
                    }
                ]
            }

        monkeypatch.setattr(
            mcp_user_client,
            "discover_mcp_server_catalog",
            _discover_second,
        )
        second_started_at = first_started_at + timedelta(seconds=1)
        second_result = await integration_service.run_remote_mcp_discovery(
            mcp_integration_id=created.id,
            trigger=MCPDiscoveryTrigger.REFRESH,
            started_at=second_started_at,
        )

        assert second_result.status == MCPDiscoveryStatus.SUCCEEDED.value
        assert second_result.catalog_version == 2

        refreshed = await integration_service.get_mcp_integration(
            mcp_integration_id=created.id
        )
        assert refreshed is not None
        assert refreshed.discovery_status == MCPDiscoveryStatus.SUCCEEDED.value
        assert refreshed.catalog_version == 2
        assert refreshed.last_discovered_at is not None
        assert refreshed.last_discovery_error_code is None
        assert refreshed.last_discovery_error_summary is None

        catalog_result = await integration_service.session.execute(
            select(MCPIntegrationCatalogEntry).where(
                MCPIntegrationCatalogEntry.mcp_integration_id == created.id
            )
        )
        catalog_entries = catalog_result.scalars().all()
        assert len(catalog_entries) == 2
        active_entries = [entry for entry in catalog_entries if entry.is_active]
        inactive_entries = [entry for entry in catalog_entries if not entry.is_active]
        assert len(active_entries) == 1
        assert active_entries[0].artifact_type == MCPCatalogArtifactType.TOOL.value
        assert active_entries[0].content_hash == "c" * 64
        assert len(inactive_entries) == 1
        assert inactive_entries[0].artifact_type == MCPCatalogArtifactType.PROMPT.value

        attempts_result = await integration_service.session.execute(
            select(MCPIntegrationDiscoveryAttempt)
            .where(MCPIntegrationDiscoveryAttempt.mcp_integration_id == created.id)
            .order_by(MCPIntegrationDiscoveryAttempt.started_at)
        )
        attempts = attempts_result.scalars().all()
        assert [attempt.status for attempt in attempts] == [
            MCPDiscoveryAttemptStatus.SUCCEEDED.value,
            MCPDiscoveryAttemptStatus.SUCCEEDED.value,
        ]
        assert attempts[0].catalog_version == 1
        assert attempts[0].artifact_counts == {
            MCPCatalogArtifactType.TOOL.value: 1,
            MCPCatalogArtifactType.RESOURCE.value: 0,
            MCPCatalogArtifactType.PROMPT.value: 1,
        }
        assert attempts[1].catalog_version == 2
        assert attempts[1].artifact_counts == {
            MCPCatalogArtifactType.TOOL.value: 1,
            MCPCatalogArtifactType.RESOURCE.value: 0,
            MCPCatalogArtifactType.PROMPT.value: 0,
        }

    async def test_run_remote_mcp_discovery_failure_marks_stale_and_keeps_catalog(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test failed discovery preserves active catalog and marks integration stale."""
        created = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Stale MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.NONE,
            )
        )
        assert created.last_discovery_attempt_at is not None

        await self._insert_catalog_entry(
            integration_service=integration_service,
            mcp_integration_id=created.id,
            integration_name=created.name,
            artifact_type=MCPCatalogArtifactType.TOOL,
            artifact_key="existing-tool-1234567890",
            artifact_ref="existing.tool",
        )
        created.catalog_version = 1
        created.discovery_status = MCPDiscoveryStatus.SUCCEEDED.value
        created.last_discovered_at = datetime.now(UTC)
        integration_service.session.add(created)
        await integration_service.session.commit()

        request = httpx.Request("GET", "https://api.example.com/mcp")

        async def _fail_discovery(_: object) -> dict[str, object]:
            raise httpx.ConnectError("connection refused", request=request)

        monkeypatch.setattr(
            mcp_user_client,
            "discover_mcp_server_catalog",
            _fail_discovery,
        )
        started_at = created.last_discovery_attempt_at + timedelta(seconds=1)
        result = await integration_service.run_remote_mcp_discovery(
            mcp_integration_id=created.id,
            trigger=MCPDiscoveryTrigger.REFRESH,
            started_at=started_at,
        )

        assert result.status == MCPDiscoveryStatus.STALE.value
        assert result.catalog_version == 1
        assert result.error_code == "connection_error"

        refreshed = await integration_service.get_mcp_integration(
            mcp_integration_id=created.id
        )
        assert refreshed is not None
        assert refreshed.discovery_status == MCPDiscoveryStatus.STALE.value
        assert refreshed.catalog_version == 1
        assert refreshed.last_discovery_error_code == "connection_error"
        assert (
            refreshed.last_discovery_error_summary
            == "Could not connect to the MCP server."
        )

        catalog_result = await integration_service.session.execute(
            select(MCPIntegrationCatalogEntry).where(
                MCPIntegrationCatalogEntry.mcp_integration_id == created.id
            )
        )
        catalog_entries = catalog_result.scalars().all()
        assert len(catalog_entries) == 1
        assert catalog_entries[0].is_active is True

        attempt_result = await integration_service.session.execute(
            select(MCPIntegrationDiscoveryAttempt).where(
                MCPIntegrationDiscoveryAttempt.mcp_integration_id == created.id
            )
        )
        attempt = attempt_result.scalars().one()
        assert attempt.status == MCPDiscoveryAttemptStatus.FAILED.value
        assert attempt.trigger == MCPDiscoveryTrigger.REFRESH.value
        assert attempt.error_code == "connection_error"

    async def test_run_remote_mcp_discovery_failure_persists_when_config_resolution_raises(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test config resolution exceptions are persisted as discovery failures."""
        created = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Broken config MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.NONE,
            )
        )
        assert created.last_discovery_attempt_at is not None

        async def _raise_config_error(**_: object) -> None:
            raise ValueError("missing server configuration")

        monkeypatch.setattr(
            integration_service,
            "resolve_mcp_http_server_config",
            _raise_config_error,
        )
        started_at = created.last_discovery_attempt_at + timedelta(seconds=1)
        result = await integration_service.run_remote_mcp_discovery(
            mcp_integration_id=created.id,
            trigger=MCPDiscoveryTrigger.REFRESH,
            started_at=started_at,
        )

        assert result.status == MCPDiscoveryStatus.FAILED.value
        assert result.catalog_version == 0
        assert result.error_code == "invalid_config"

        refreshed = await integration_service.get_mcp_integration(
            mcp_integration_id=created.id
        )
        assert refreshed is not None
        assert refreshed.discovery_status == MCPDiscoveryStatus.FAILED.value
        assert refreshed.last_discovery_error_code == "invalid_config"
        assert (
            refreshed.last_discovery_error_summary
            == "The MCP integration configuration is incomplete."
        )

        attempt_result = await integration_service.session.execute(
            select(MCPIntegrationDiscoveryAttempt).where(
                MCPIntegrationDiscoveryAttempt.mcp_integration_id == created.id
            )
        )
        attempt = attempt_result.scalars().one()
        assert attempt.status == MCPDiscoveryAttemptStatus.FAILED.value
        assert attempt.trigger == MCPDiscoveryTrigger.REFRESH.value
        assert attempt.error_code == "invalid_config"

    async def test_run_remote_mcp_discovery_keeps_artifact_keys_stable_when_display_name_changes(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test display-name changes reuse the same catalog row."""
        created = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Stable key MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.NONE,
            )
        )
        assert created.last_discovery_attempt_at is not None

        async def _discover_first(_: object) -> dict[str, object]:
            return {
                "artifacts": [
                    {
                        "artifact_type": MCPCatalogArtifactType.TOOL.value,
                        "artifact_ref": "search",
                        "display_name": "Search",
                        "description": "Search everything",
                        "input_schema": {"type": "object"},
                        "metadata": {"source": "tool"},
                        "raw_payload": {"name": "search"},
                        "content_hash": "d" * 64,
                    }
                ]
            }

        monkeypatch.setattr(
            mcp_user_client,
            "discover_mcp_server_catalog",
            _discover_first,
        )
        first_started_at = created.last_discovery_attempt_at + timedelta(seconds=1)
        first_result = await integration_service.run_remote_mcp_discovery(
            mcp_integration_id=created.id,
            trigger=MCPDiscoveryTrigger.CREATE,
            started_at=first_started_at,
        )
        assert first_result.status == MCPDiscoveryStatus.SUCCEEDED.value

        first_catalog_result = await integration_service.session.execute(
            select(MCPIntegrationCatalogEntry).where(
                MCPIntegrationCatalogEntry.mcp_integration_id == created.id
            )
        )
        first_catalog_entries = first_catalog_result.scalars().all()
        assert len(first_catalog_entries) == 1
        original_artifact_key = first_catalog_entries[0].artifact_key

        async def _discover_second(_: object) -> dict[str, object]:
            return {
                "artifacts": [
                    {
                        "artifact_type": MCPCatalogArtifactType.TOOL.value,
                        "artifact_ref": "search",
                        "display_name": "Search docs",
                        "description": "Search everything",
                        "input_schema": {"type": "object"},
                        "metadata": {"source": "tool"},
                        "raw_payload": {"name": "search"},
                        "content_hash": "e" * 64,
                    }
                ]
            }

        monkeypatch.setattr(
            mcp_user_client,
            "discover_mcp_server_catalog",
            _discover_second,
        )
        second_started_at = first_started_at + timedelta(seconds=1)
        second_result = await integration_service.run_remote_mcp_discovery(
            mcp_integration_id=created.id,
            trigger=MCPDiscoveryTrigger.REFRESH,
            started_at=second_started_at,
        )
        assert second_result.status == MCPDiscoveryStatus.SUCCEEDED.value
        assert second_result.catalog_version == 2

        second_catalog_result = await integration_service.session.execute(
            select(MCPIntegrationCatalogEntry)
            .where(MCPIntegrationCatalogEntry.mcp_integration_id == created.id)
            .execution_options(populate_existing=True)
        )
        second_catalog_entries = second_catalog_result.scalars().all()
        assert len(second_catalog_entries) == 1
        assert second_catalog_entries[0].artifact_key == original_artifact_key
        assert second_catalog_entries[0].display_name == "Search docs"
        assert second_catalog_entries[0].is_active is True

    async def test_run_local_mcp_discovery_persists_catalog(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test local stdio discovery persists normalized catalog rows."""
        created = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Local catalog MCP",
                stdio_command="uvx",
                stdio_args=["example-mcp"],
            )
        )
        assert created.last_discovery_attempt_at is not None

        async def _discover_local(_: object) -> MCPServerCatalog:
            return MCPServerCatalog(
                server_name="local-scope",
                tools=(
                    {
                        "artifact_type": MCPCatalogArtifactType.TOOL.value,
                        "artifact_ref": "search",
                        "display_name": "Search",
                        "description": "Search everything",
                        "input_schema": {"type": "object"},
                        "metadata": {"source": "tool"},
                        "raw_payload": {"name": "search"},
                        "content_hash": "d" * 64,
                    },
                ),
                resources=(
                    {
                        "artifact_type": MCPCatalogArtifactType.RESOURCE.value,
                        "artifact_ref": "resource://playbook",
                        "display_name": "Playbook",
                        "description": "Playbook resource",
                        "input_schema": None,
                        "metadata": {"source": "resource"},
                        "raw_payload": {"uri": "resource://playbook"},
                        "content_hash": "e" * 64,
                    },
                ),
                prompts=(),
            )

        monkeypatch.setattr(
            "tracecat.integrations.service.discover_local_mcp_server_catalog",
            _discover_local,
        )
        started_at = created.last_discovery_attempt_at + timedelta(seconds=1)
        result = await integration_service.run_local_mcp_discovery(
            mcp_integration_id=created.id,
            trigger=MCPDiscoveryTrigger.CREATE,
            started_at=started_at,
        )

        assert result.status == MCPDiscoveryStatus.SUCCEEDED.value
        assert result.catalog_version == 1

        refreshed = await integration_service.get_mcp_integration(
            mcp_integration_id=created.id
        )
        assert refreshed is not None
        assert refreshed.discovery_status == MCPDiscoveryStatus.SUCCEEDED.value
        assert refreshed.catalog_version == 1
        assert refreshed.last_discovery_error_code is None

        catalog_result = await integration_service.session.execute(
            select(MCPIntegrationCatalogEntry).where(
                MCPIntegrationCatalogEntry.mcp_integration_id == created.id
            )
        )
        catalog_entries = catalog_result.scalars().all()
        assert len(catalog_entries) == 2
        assert {entry.artifact_type for entry in catalog_entries} == {
            MCPCatalogArtifactType.TOOL.value,
            MCPCatalogArtifactType.RESOURCE.value,
        }

    async def test_run_local_mcp_discovery_failure_persists_phase_specific_error(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test local stdio discovery stores a phase-specific error code."""
        created = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Broken local MCP",
                stdio_command="uvx",
                stdio_args=["broken-mcp"],
            )
        )
        assert created.last_discovery_attempt_at is not None

        async def _fail_local(_: object) -> MCPServerCatalog:
            raise LocalMCPDiscoveryError(
                phase=LocalMCPDiscoveryPhase.LIST_TOOLS,
                summary="The local MCP server failed while listing tools.",
                details={"stderr": "boom"},
            )

        monkeypatch.setattr(
            "tracecat.integrations.service.discover_local_mcp_server_catalog",
            _fail_local,
        )
        started_at = created.last_discovery_attempt_at + timedelta(seconds=1)
        result = await integration_service.run_local_mcp_discovery(
            mcp_integration_id=created.id,
            trigger=MCPDiscoveryTrigger.REFRESH,
            started_at=started_at,
        )

        assert result.status == MCPDiscoveryStatus.FAILED.value
        assert result.error_code == LocalMCPDiscoveryPhase.LIST_TOOLS.value

        refreshed = await integration_service.get_mcp_integration(
            mcp_integration_id=created.id
        )
        assert refreshed is not None
        assert refreshed.discovery_status == MCPDiscoveryStatus.FAILED.value
        assert (
            refreshed.last_discovery_error_code
            == LocalMCPDiscoveryPhase.LIST_TOOLS.value
        )

    async def test_update_mcp_integration_partial(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test that partial updates work correctly."""
        params = MCPHttpIntegrationCreate(
            name="Test MCP",
            description="Original description",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )
        created = await integration_service.create_mcp_integration(params=params)

        # Update only description
        update_params = MCPIntegrationUpdate(description="Updated description")
        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=created.id, params=update_params
        )

        assert updated is not None
        assert updated.name == created.name  # Unchanged
        assert updated.description == "Updated description"
        assert updated.server_uri == created.server_uri  # Unchanged
        assert updated.auth_type == created.auth_type  # Unchanged

    async def test_delete_mcp_integration(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test deleting an MCP integration."""
        params = MCPHttpIntegrationCreate(
            name="Test MCP",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )
        created = await integration_service.create_mcp_integration(params=params)

        deleted = await integration_service.delete_mcp_integration(
            mcp_integration_id=created.id
        )

        assert deleted is True

        # Verify it's gone
        retrieved = await integration_service.get_mcp_integration(
            mcp_integration_id=created.id
        )
        assert retrieved is None

    async def test_delete_mcp_integration_shared_oauth_keeps_tokens(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test deleting one MCP integration keeps shared OAuth tokens."""
        first = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="First MCP",
                server_uri="https://api.example.com/mcp-1",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )
        second = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Second MCP",
                server_uri="https://api.example.com/mcp-2",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )

        deleted = await integration_service.delete_mcp_integration(
            mcp_integration_id=first.id
        )
        assert deleted is True

        remaining = await integration_service.get_mcp_integration(
            mcp_integration_id=second.id
        )
        assert remaining is not None

        refreshed_oauth = await integration_service.session.get(
            OAuthIntegration, oauth_integration.id
        )
        assert refreshed_oauth is not None
        assert await integration_service.get_access_token(refreshed_oauth) is not None

    async def test_delete_mcp_integration_last_reference_regular_oauth_keeps_tokens(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test deleting last reference does not clear non-MCP provider tokens."""
        created = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Regular OAuth MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )

        await integration_service.delete_mcp_integration(mcp_integration_id=created.id)

        refreshed_oauth = await integration_service.session.get(
            OAuthIntegration, oauth_integration.id
        )
        assert refreshed_oauth is not None
        assert await integration_service.get_access_token(refreshed_oauth) is not None

    async def test_delete_mcp_integration_last_reference_disconnects_mcp_provider_oauth(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test deleting the last MCP reference disconnects MCP-provider OAuth tokens."""
        provider_key = ProviderKey(
            id="github_mcp",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )
        oauth_integration = await integration_service.store_integration(
            provider_key=provider_key,
            access_token=SecretStr("test_access_token"),
            refresh_token=SecretStr("test_refresh_token"),
            expires_in=3600,
        )

        auto_created = await integration_service.session.execute(
            select(MCPIntegration).where(
                MCPIntegration.workspace_id == integration_service.workspace_id,
                MCPIntegration.oauth_integration_id == oauth_integration.id,
            )
        )
        mcp_integration = auto_created.scalars().first()
        assert mcp_integration is not None
        assert mcp_integration.slug == "github_mcp"
        assert len(mcp_integration.scope_namespace) == 16
        assert mcp_integration.discovery_status == MCPDiscoveryStatus.PENDING.value
        assert mcp_integration.last_discovery_attempt_at is not None
        assert mcp_integration.catalog_version == 0

        await integration_service.delete_mcp_integration(
            mcp_integration_id=mcp_integration.id
        )

        refreshed_oauth = await integration_service.session.get(
            OAuthIntegration, oauth_integration.id
        )
        assert refreshed_oauth is not None
        assert await integration_service.get_access_token(refreshed_oauth) is None
        assert refreshed_oauth.encrypted_access_token == b""
        assert refreshed_oauth.encrypted_refresh_token is None
        assert refreshed_oauth.expires_at is None
        assert refreshed_oauth.scope is None
        assert refreshed_oauth.requested_scopes is None

    async def test_delete_mcp_integration_rolls_back_on_disconnect_failure(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test delete rollback preserves MCP and preset references on DB failure."""
        created = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Rollback MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )
        created_id = created.id

        preset = AgentPreset(
            workspace_id=integration_service.workspace_id,
            name="Rollback preset",
            slug="rollback-preset",
            model_name="gpt-4o-mini",
            model_provider="openai",
            mcp_integrations=[str(created_id)],
        )
        integration_service.session.add(preset)
        await integration_service.session.commit()
        preset_id = preset.id

        conflicting_preset = AgentPreset(
            workspace_id=integration_service.workspace_id,
            name="Rollback preset conflict",
            slug="rollback-preset",
            model_name="gpt-4o-mini",
            model_provider="openai",
        )
        integration_service.session.add(conflicting_preset)

        integration_service.session.autoflush = False
        try:
            with pytest.raises(IntegrityError):
                await integration_service.delete_mcp_integration(
                    mcp_integration_id=created_id
                )
        finally:
            integration_service.session.autoflush = True

        existing_mcp_result = await integration_service.session.execute(
            select(MCPIntegration).where(MCPIntegration.id == created_id)
        )
        existing_mcp = existing_mcp_result.scalars().first()
        assert existing_mcp is not None

        refreshed_preset_result = await integration_service.session.execute(
            select(AgentPreset).where(AgentPreset.id == preset_id)
        )
        refreshed_preset = refreshed_preset_result.scalars().first()
        assert refreshed_preset is not None
        assert refreshed_preset.mcp_integrations is not None
        assert str(created_id) in refreshed_preset.mcp_integrations


@pytest.mark.anyio
class TestMCPIntegrationAuthTypeSwapping:
    """Test authentication type switching and credential swapping."""

    async def test_switch_from_none_to_oauth2(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test switching from no auth to OAuth2."""
        params = MCPHttpIntegrationCreate(
            name="Test MCP",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.NONE,
        )
        created = await integration_service.create_mcp_integration(params=params)

        # Switch to OAuth2
        update_params = MCPIntegrationUpdate(
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )
        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=created.id, params=update_params
        )

        assert updated is not None
        assert updated.auth_type == MCPAuthType.OAUTH2
        assert updated.oauth_integration_id == oauth_integration.id
        assert updated.encrypted_headers is None

    async def test_switch_from_oauth2_to_custom(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test switching from OAuth2 to custom auth."""
        params = MCPHttpIntegrationCreate(
            name="Test MCP",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )
        created = await integration_service.create_mcp_integration(params=params)

        # Switch to custom
        update_params = MCPIntegrationUpdate(
            auth_type=MCPAuthType.CUSTOM,
            custom_credentials=SecretStr('{"Authorization": "Bearer token"}'),
        )
        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=created.id, params=update_params
        )

        assert updated is not None
        assert updated.auth_type == MCPAuthType.CUSTOM
        assert updated.encrypted_headers is not None
        # OAuth integration ID should still be set but not used
        assert updated.oauth_integration_id == oauth_integration.id

    async def test_switch_from_custom_to_none(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test switching from custom auth to no auth."""
        params = MCPHttpIntegrationCreate(
            name="Test MCP",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.CUSTOM,
            custom_credentials=SecretStr('{"Authorization": "Bearer token"}'),
        )
        created = await integration_service.create_mcp_integration(params=params)

        # Switch to none
        update_params = MCPIntegrationUpdate(auth_type=MCPAuthType.NONE)
        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=created.id, params=update_params
        )

        assert updated is not None
        assert updated.auth_type == MCPAuthType.NONE
        assert updated.encrypted_headers is None

    async def test_update_custom_credentials(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test updating custom credentials without changing auth type."""
        old_creds = '{"Authorization": "Bearer old_token"}'
        params = MCPHttpIntegrationCreate(
            name="Test MCP",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.CUSTOM,
            custom_credentials=SecretStr(old_creds),
        )
        created = await integration_service.create_mcp_integration(params=params)
        old_encrypted_headers = created.encrypted_headers

        # Update credentials
        new_creds = '{"Authorization": "Bearer new_token"}'
        update_params = MCPIntegrationUpdate(custom_credentials=SecretStr(new_creds))
        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=created.id, params=update_params
        )

        assert updated is not None
        assert updated.auth_type == MCPAuthType.CUSTOM
        assert updated.encrypted_headers is not None
        assert updated.encrypted_headers != old_encrypted_headers

    async def test_swap_oauth_integration(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test swapping OAuth integration reference."""
        params = MCPHttpIntegrationCreate(
            name="Test MCP",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )
        created = await integration_service.create_mcp_integration(params=params)

        # Create a second OAuth integration
        provider_key = ProviderKey(
            id="gitlab",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )
        oauth_integration2 = await integration_service.store_integration(
            provider_key=provider_key,
            access_token=SecretStr("test_access_token_2"),
            refresh_token=SecretStr("test_refresh_token_2"),
            expires_in=3600,
        )

        # Swap to the new OAuth integration
        update_params = MCPIntegrationUpdate(oauth_integration_id=oauth_integration2.id)
        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=created.id, params=update_params
        )

        assert updated is not None
        assert updated.oauth_integration_id == oauth_integration2.id

    async def test_oauth2_headers_preserved_when_updating_oauth2(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test OAUTH2 headers are preserved when auth_type remains OAUTH2."""
        params = MCPHttpIntegrationCreate(
            name="Test MCP",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
            custom_credentials=SecretStr('{"X-Wiz-Tenant": "tenant-a"}'),
        )
        created = await integration_service.create_mcp_integration(params=params)
        original_encrypted_headers = created.encrypted_headers

        update_params = MCPIntegrationUpdate(
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )
        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=created.id, params=update_params
        )

        assert updated is not None
        assert updated.auth_type == MCPAuthType.OAUTH2
        assert updated.encrypted_headers == original_encrypted_headers

    async def test_oauth2_headers_cleared_with_empty_custom_credentials(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test OAUTH2 additional headers clear when updated with empty credentials."""
        params = MCPHttpIntegrationCreate(
            name="Test MCP",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
            custom_credentials=SecretStr('{"X-Wiz-Tenant": "tenant-a"}'),
        )
        created = await integration_service.create_mcp_integration(params=params)
        assert created.encrypted_headers is not None

        update_params = MCPIntegrationUpdate(custom_credentials=SecretStr(""))
        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=created.id, params=update_params
        )

        assert updated is not None
        assert updated.auth_type == MCPAuthType.OAUTH2
        assert updated.encrypted_headers is None


@pytest.mark.anyio
class TestMCPIntegrationValidation:
    """Test validation constraints and error handling."""

    async def test_legacy_http_payload_without_server_type_is_accepted(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test legacy HTTP create payloads still parse without server_type."""
        params = TypeAdapter(MCPIntegrationCreate).validate_python(
            {
                "name": "Legacy HTTP MCP",
                "server_uri": "https://api.example.com/mcp",
                "auth_type": MCPAuthType.NONE,
            }
        )

        assert isinstance(params, MCPHttpIntegrationCreate)
        assert params.server_type == "http"

        created = await integration_service.create_mcp_integration(params=params)
        assert created.server_type == "http"
        assert created.server_uri == "https://api.example.com/mcp"

    async def test_create_stdio_rejects_disallowed_command(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test stdio create rejects commands outside allowlist."""
        params = MCPStdioIntegrationCreate(
            name="Unsafe Stdio MCP",
            stdio_command="bash",
            stdio_args=["-lc", "echo test"],
        )

        with pytest.raises(ValueError, match="is not allowed"):
            await integration_service.create_mcp_integration(params=params)

    async def test_update_stdio_rejects_unsafe_args(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test stdio update rejects unsafe argument values."""
        created = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Safe Stdio MCP",
                stdio_command="npx",
                stdio_args=["@modelcontextprotocol/server-github"],
            )
        )

        with pytest.raises(ValueError, match="dangerous pattern"):
            await integration_service.update_mcp_integration(
                mcp_integration_id=created.id,
                params=MCPIntegrationUpdate(stdio_args=["$(whoami)"]),
            )

        refreshed = await integration_service.get_mcp_integration(
            mcp_integration_id=created.id
        )
        assert refreshed is not None
        assert refreshed.stdio_args == ["@modelcontextprotocol/server-github"]

    async def test_oauth2_requires_oauth_integration_id(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test that OAuth2 auth requires oauth_integration_id."""
        params = MCPHttpIntegrationCreate(
            name="Test MCP",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=None,
        )

        with pytest.raises(ValueError, match="oauth_integration_id is required"):
            await integration_service.create_mcp_integration(params=params)

    async def test_oauth2_validates_oauth_integration_exists(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test that OAuth2 validates oauth_integration_id exists."""
        params = MCPHttpIntegrationCreate(
            name="Test MCP",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=uuid.uuid4(),
        )

        with pytest.raises(ValueError, match="OAuth integration not found"):
            await integration_service.create_mcp_integration(params=params)

    async def test_server_uri_validation_missing_scheme(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test server URI validation for missing scheme."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Server URI must"):
            MCPHttpIntegrationCreate(
                name="Test MCP",
                server_uri="api.example.com/mcp",  # Missing http://
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )

    async def test_server_uri_validation_invalid_scheme(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test server URI validation for invalid scheme."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Server URI must use HTTP or HTTPS"):
            MCPHttpIntegrationCreate(
                name="Test MCP",
                server_uri="ftp://api.example.com/mcp",  # Wrong scheme
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )

    async def test_server_uri_validation_http_allowed(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test that HTTP is allowed for server URI (e.g., localhost)."""
        params = MCPHttpIntegrationCreate(
            name="Test MCP",
            server_uri="http://localhost:8000/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )

        mcp_integration = await integration_service.create_mcp_integration(
            params=params
        )
        assert mcp_integration.server_uri == "http://localhost:8000/mcp"

    async def test_name_length_validation_too_short(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test that name length is validated (too short)."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            MCPHttpIntegrationCreate(
                name="AB",  # Less than 3 characters
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )

    async def test_name_length_validation_minimum(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test minimum valid name length."""
        params = MCPHttpIntegrationCreate(
            name="ABC",  # Exactly 3 characters
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )

        mcp_integration = await integration_service.create_mcp_integration(
            params=params
        )
        assert mcp_integration.name == "ABC"

    async def test_slug_uniqueness(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test that slug uniqueness is enforced within a workspace."""
        params1 = MCPHttpIntegrationCreate(
            name="Test MCP",
            server_uri="https://api1.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )
        mcp1 = await integration_service.create_mcp_integration(params=params1)
        assert mcp1.slug == "test-mcp"

        # Same name should generate unique slug
        params2 = MCPHttpIntegrationCreate(
            name="Test MCP",
            server_uri="https://api2.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )
        mcp2 = await integration_service.create_mcp_integration(params=params2)
        assert mcp2.slug == "test-mcp-1"  # Suffix added for uniqueness

    async def test_slug_generation(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test that slugs are generated correctly from names."""
        test_cases = [
            ("Simple Name", "simple-name"),
            ("Name With Numbers 123", "name-with-numbers-123"),
            ("Special!@# Characters", "special-characters"),
            ("  Leading/Trailing Spaces  ", "leading-trailing-spaces"),
            ("UPPERCASE NAME", "uppercase-name"),
        ]

        for name, expected_slug in test_cases:
            params = MCPHttpIntegrationCreate(
                name=name,
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
            mcp_integration = await integration_service.create_mcp_integration(
                params=params
            )
            assert mcp_integration.slug == expected_slug

    async def test_requested_slug_preserves_underscores_on_fallback(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test requested_slug fallback preserves underscore-based provider IDs."""
        existing = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name="Existing MCP",
            description=None,
            slug="github_mcp",
            scope_namespace="0" * 16,
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.NONE,
            oauth_integration_id=None,
            encrypted_headers=None,
        )
        integration_service.session.add(existing)
        await integration_service.session.commit()

        slug = await integration_service._generate_mcp_integration_slug(
            name="GitHub MCP",
            requested_slug="github_mcp",
        )
        assert slug == "github_mcp-1"

    async def test_update_nonexistent_integration(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test updating a non-existent MCP integration."""
        non_existent_id = uuid.uuid4()
        update_params = MCPIntegrationUpdate(name="Updated Name")

        result = await integration_service.update_mcp_integration(
            mcp_integration_id=non_existent_id, params=update_params
        )
        assert result is None


@pytest.mark.anyio
class TestMCPIntegrationWorkspaceIsolation:
    """Test that MCP integrations are properly isolated by workspace."""

    async def test_mcp_integrations_isolated_by_workspace(
        self,
        session: AsyncSession,
        svc_role: Role,
        svc_workspace,
    ) -> None:
        """Test that MCP integrations are isolated by workspace."""
        from tracecat.db.models import Workspace

        # Create service for workspace 1
        service1 = IntegrationService(session=session, role=svc_role)

        # Create OAuth integration in workspace 1
        provider_key = ProviderKey(
            id="github",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )
        oauth1 = await service1.store_integration(
            provider_key=provider_key,
            access_token=SecretStr("token1"),
            refresh_token=SecretStr("refresh1"),
            expires_in=3600,
        )

        # Create MCP integration in workspace 1
        params1 = MCPHttpIntegrationCreate(
            name="Workspace 1 MCP",
            server_uri="https://api1.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth1.id,
        )
        mcp1 = await service1.create_mcp_integration(params=params1)

        # Create workspace 2 (using the same organization as workspace 1)
        workspace2 = Workspace(
            name="test-workspace-2",
            organization_id=svc_workspace.organization_id,
        )
        session.add(workspace2)
        await session.flush()

        role2 = Role(
            type="user",
            workspace_id=workspace2.id,
            organization_id=svc_workspace.organization_id,
            user_id=svc_role.user_id,
            service_id="tracecat-api",
            scopes=ADMIN_SCOPES,
        )
        service2 = IntegrationService(session=session, role=role2)

        # List integrations in workspace 2 - should be empty
        integrations2 = await service2.list_mcp_integrations()
        assert len(integrations2) == 0

        # Try to get MCP integration from workspace 1 in workspace 2 - should fail
        retrieved = await service2.get_mcp_integration(mcp_integration_id=mcp1.id)
        assert retrieved is None

        # Cleanup
        await session.delete(workspace2)
        await session.commit()

    async def test_cannot_reference_oauth_from_different_workspace(
        self,
        session: AsyncSession,
        svc_role: Role,
        svc_workspace,
    ) -> None:
        """Test that MCP integration cannot reference OAuth integration from different workspace."""
        from tracecat.db.models import Workspace

        # Create service for workspace 1
        service1 = IntegrationService(session=session, role=svc_role)

        # Create OAuth integration in workspace 1
        provider_key = ProviderKey(
            id="github",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )
        oauth1 = await service1.store_integration(
            provider_key=provider_key,
            access_token=SecretStr("token1"),
            refresh_token=SecretStr("refresh1"),
            expires_in=3600,
        )

        # Create workspace 2 (using the same organization as workspace 1)
        workspace2 = Workspace(
            name="test-workspace-2",
            organization_id=svc_workspace.organization_id,
        )
        session.add(workspace2)
        await session.flush()

        role2 = Role(
            type="user",
            workspace_id=workspace2.id,
            organization_id=svc_workspace.organization_id,
            user_id=svc_role.user_id,
            service_id="tracecat-api",
            scopes=ADMIN_SCOPES,
        )
        service2 = IntegrationService(session=session, role=role2)

        # Try to create MCP integration in workspace 2 using OAuth from workspace 1
        params = MCPHttpIntegrationCreate(
            name="Workspace 2 MCP",
            server_uri="https://api2.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth1.id,  # From workspace 1
        )

        with pytest.raises(ValueError, match="does not belong to workspace"):
            await service2.create_mcp_integration(params=params)

        # Cleanup
        await session.delete(workspace2)
        await session.commit()


@pytest.mark.anyio
class TestMCPIntegrationEdgeCases:
    """Test edge cases and special scenarios."""

    async def test_create_with_empty_description(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test creating MCP integration with empty description."""
        params = MCPHttpIntegrationCreate(
            name="Test MCP",
            description="",  # Empty string
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )

        mcp_integration = await integration_service.create_mcp_integration(
            params=params
        )
        assert mcp_integration.description is None  # Empty string converted to None

    async def test_whitespace_trimming(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """Test that whitespace is stripped from inputs."""
        params = MCPHttpIntegrationCreate(
            name="  Test MCP  ",
            description="  Test description  ",
            server_uri="  https://api.example.com/mcp  ",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )

        mcp_integration = await integration_service.create_mcp_integration(
            params=params
        )

        assert mcp_integration.name == "Test MCP"
        assert mcp_integration.description == "Test description"
        assert mcp_integration.server_uri == "https://api.example.com/mcp"

    async def test_switching_to_oauth2_without_integration_id(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test that switching to OAuth2 without providing oauth_integration_id fails."""
        params = MCPHttpIntegrationCreate(
            name="Test MCP",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.NONE,
        )
        created = await integration_service.create_mcp_integration(params=params)

        # Try to switch to OAuth2 without providing oauth_integration_id
        update_params = MCPIntegrationUpdate(auth_type=MCPAuthType.OAUTH2)

        with pytest.raises(ValueError, match="oauth_integration_id is required"):
            await integration_service.update_mcp_integration(
                mcp_integration_id=created.id, params=update_params
            )


@pytest.mark.anyio
class TestMCPProviderOAuth:
    """Test MCP OAuth provider behavior and OAuth discovery."""

    async def test_wiz_provider_resource_uses_trailing_slash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wiz provider must send resource with trailing slash for OAuth calls."""
        monkeypatch.setenv("TRACECAT__PUBLIC_APP_URL", "https://app.test")
        provider = WizMCPProvider(
            client_id="wiz-client",
            client_secret="wiz-secret",
            discovered_auth_endpoint="https://mcp.app.wiz.io/oauth/authorize",
            discovered_token_endpoint="https://mcp.app.wiz.io/oauth/token",
        )

        assert provider._get_additional_authorize_params()["resource"] == (
            "https://mcp.app.wiz.io/"
        )
        assert provider._get_additional_token_params()["resource"] == (
            "https://mcp.app.wiz.io/"
        )

    async def test_mcp_provider_preserves_token_methods(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that MCP providers preserve token endpoint authentication methods."""

        # Create a dummy MCP provider for testing
        class DummyMCPProvider(MCPAuthProvider):
            id: str = "dummy_mcp"  # type: ignore[assignment]
            mcp_server_uri: str = "https://dummy.example/mcp"  # type: ignore[assignment]
            scopes: ProviderScopes = ProviderScopes(default=[])  # type: ignore[assignment]
            metadata: ProviderMetadata = ProviderMetadata(  # type: ignore[assignment]
                id="dummy_mcp",
                name="Dummy MCP",
                description="Dummy MCP provider for tests",
                requires_config=False,
                enabled=True,
            )

        monkeypatch.setenv("TRACECAT__PUBLIC_APP_URL", "https://app.test")

        discovery = OAuthDiscoveryResult(
            authorization_endpoint="https://dummy.example/oauth/authorize",
            token_endpoint="https://dummy.example/oauth/token",
            token_methods=["client_secret_post"],
            registration_endpoint=None,
        )

        async def fake_discover(
            cls,
            logger_instance,
            *,
            discovered_auth_endpoint=None,
            discovered_token_endpoint=None,
        ) -> OAuthDiscoveryResult:
            return discovery

        monkeypatch.setattr(
            DummyMCPProvider,
            "_discover_oauth_endpoints_async",
            classmethod(fake_discover),
        )

        provider_config = ProviderConfig(
            client_id="dummy-client",
            client_secret=SecretStr("dummy-secret"),
            authorization_endpoint=discovery.authorization_endpoint,
            token_endpoint=discovery.token_endpoint,
            scopes=[],
        )

        provider = await DummyMCPProvider.instantiate(config=provider_config)

        assert provider._token_endpoint_auth_methods_supported == ["client_secret_post"]
        assert (
            getattr(provider.client, "token_endpoint_auth_method", None)
            == "client_secret_post"
        )

    async def test_sentry_provider_uses_mcp_resource_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sentry MCP must send a /mcp resource target to avoid invalid_target."""

        discovery = OAuthDiscoveryResult(
            authorization_endpoint="https://mcp.sentry.dev/oauth/authorize",
            token_endpoint="https://mcp.sentry.dev/oauth/token",
            token_methods=["none"],
            registration_endpoint="https://mcp.sentry.dev/oauth/register",
        )

        async def fake_discover(
            cls,
            logger_instance,
            *,
            discovered_auth_endpoint=None,
            discovered_token_endpoint=None,
        ) -> OAuthDiscoveryResult:
            _ = (
                cls,
                logger_instance,
                discovered_auth_endpoint,
                discovered_token_endpoint,
            )
            return discovery

        monkeypatch.setattr(
            SentryMCPProvider,
            "_discover_oauth_endpoints_async",
            classmethod(fake_discover),
        )

        provider = await SentryMCPProvider.instantiate(client_id="dummy-client")

        auth_url, _ = await provider.get_authorization_url(state="test-state")
        auth_query = parse_qs(urlparse(auth_url).query)

        assert auth_query["resource"] == [SentryMCPProvider.mcp_server_uri]
        assert (
            provider._get_additional_token_params()["resource"]
            == SentryMCPProvider.mcp_server_uri
        )
