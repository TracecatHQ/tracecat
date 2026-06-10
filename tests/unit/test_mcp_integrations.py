"""Test suite for MCP integrations.

This test suite covers MCP integration functionality including:
- CRUD operations for all auth types (OAuth2, Custom, None)
- Authentication type switching and credential swapping
- Workspace isolation
- Validation and edge cases
- MCP provider OAuth discovery behavior
"""

import socket
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr, TypeAdapter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import tracecat.integrations.catalog.service as catalog_service_module
import tracecat.integrations.service as integration_service_module
from tracecat.agent.preset.service import AgentPresetService
from tracecat.agent.session.types import AgentSessionEntity
from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.db.models import (
    AgentPreset,
    AgentPresetVersion,
    AgentSession,
    MCPIntegration,
    OAuthIntegration,
    OAuthStateDB,
    User,
)
from tracecat.exceptions import EntitlementRequired
from tracecat.integrations.catalog.loader import catalog_id_for_slug
from tracecat.integrations.catalog.service import PlatformMCPCatalogService
from tracecat.integrations.enums import MCPAuthType, OAuthGrantType
from tracecat.integrations.providers.base import (
    DynamicRegistrationResult,
    MCPAuthProvider,
    OAuthDiscoveryResult,
)
from tracecat.integrations.providers.runreveal.mcp import RunRevealMCPProvider
from tracecat.integrations.providers.sentry.mcp import SentryMCPProvider
from tracecat.integrations.providers.wiz.mcp import WizMCPProvider
from tracecat.integrations.schemas import (
    CustomOAuthProviderCreate,
    MCPConnectionOption,
    MCPConnectionSpec,
    MCPHttpIntegrationCreate,
    MCPHTTPOAuth2ConnectionSpec,
    MCPIntegrationCreate,
    MCPIntegrationUpdate,
    MCPStdioIntegrationCreate,
    ProviderConfig,
    ProviderKey,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.service import IntegrationService
from tracecat.integrations.types import OAuthServerMetadata
from tracecat.tiers import defaults as tier_defaults

pytestmark = pytest.mark.usefixtures("db")

_MCP_CONNECTION_SPEC_ADAPTER: TypeAdapter[MCPConnectionSpec] = TypeAdapter(
    MCPConnectionSpec
)


class _TestCatalogEntry(dict):
    """Dict-backed catalog entry with attribute access for terse tests."""

    def __getattr__(self, name: str):
        return self[name]


def _catalog_entry(
    *,
    slug: str,
    name: str,
    description: str,
    category: str = "Test",
    status: str = "available",
    provider_id: str | None = None,
    docs_url: str | None = None,
    connection_spec: MCPConnectionSpec | dict | None = None,
    connection_options: list[MCPConnectionOption | dict] | None = None,
    sort_key: str = "0000:test",
) -> _TestCatalogEntry:
    typed_connection_spec = (
        _MCP_CONNECTION_SPEC_ADAPTER.validate_python(connection_spec)
        if connection_spec is not None
        else None
    )
    typed_connection_options = (
        [MCPConnectionOption.model_validate(option) for option in connection_options]
        if connection_options is not None
        else None
    )
    return _TestCatalogEntry(
        id=catalog_id_for_slug(slug),
        slug=slug,
        name=name,
        description=description,
        category=category,
        status=status,
        icon_url=None,
        docs_url=docs_url,
        provider_id=provider_id,
        connection_spec=typed_connection_spec,
        connection_options=typed_connection_options,
        sort_key=sort_key,
    )


def _install_catalog_entry(
    monkeypatch: pytest.MonkeyPatch,
    catalog: _TestCatalogEntry,
) -> None:
    public_catalog = _TestCatalogEntry(
        **{
            **catalog,
            "docs_url": None,
            "provider_id": None,
            "connection_spec": None,
            "connection_options": None,
        }
    )

    def _get_entry_by_slug(slug: str, *, include_private: bool = False):
        if slug != catalog.slug:
            return None
        return catalog if include_private else public_catalog

    def _entries(*, include_private: bool = False):
        return [catalog if include_private else public_catalog]

    monkeypatch.setattr(
        integration_service_module,
        "get_platform_mcp_catalog_entry_by_slug",
        _get_entry_by_slug,
    )
    monkeypatch.setattr(
        integration_service_module,
        "get_platform_mcp_catalog_entries",
        _entries,
    )
    monkeypatch.setattr(
        catalog_service_module,
        "get_platform_mcp_catalog_entries",
        _entries,
    )


@pytest.fixture
async def integration_service(
    session: AsyncSession, svc_role: Role
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

    async def test_create_mcp_integration_with_oauth2(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
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
        assert mcp_integration.server_uri == "https://api.example.com/mcp"
        assert mcp_integration.auth_type == MCPAuthType.OAUTH2
        assert mcp_integration.oauth_integration_id == oauth_integration.id
        assert mcp_integration.encrypted_headers is None
        assert mcp_integration.created_at is not None
        assert mcp_integration.updated_at is not None

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

    async def test_connect_platform_mcp_catalog_creates_default_http_row(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Catalog Connect creates an idempotent MCP row when defaults suffice."""
        catalog = _catalog_entry(
            slug="default-http-mcp",
            name="Default HTTP MCP",
            description="Default HTTP catalog row",
            connection_spec={
                "kind": "http_none",
                "server_type": "http",
                "auth_type": "NONE",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "server_uri": "https://mcp.example.com/mcp",
            },
            sort_key="0000:default-http-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)

        first_result = await integration_service.connect_platform_mcp_catalog(
            catalog_slug=catalog.slug
        )
        second_result = await integration_service.connect_platform_mcp_catalog(
            catalog_slug=catalog.slug
        )
        first = first_result.mcp_integration
        second = second_result.mcp_integration

        assert first is not None
        assert second is not None
        assert first.id == second.id
        assert first.slug == catalog.slug
        assert first.catalog_slug == catalog.slug
        assert first.name == catalog.name
        assert first.server_type == "http"
        assert first.server_uri == "https://mcp.example.com/mcp"
        assert first.auth_type == MCPAuthType.NONE

    async def test_platform_mcp_catalog_redacts_locked_rows_without_entitlement(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Locked catalog rows stay visible but hide setup details."""
        catalog = _catalog_entry(
            slug="locked-http-mcp",
            name="Locked HTTP MCP",
            description="Locked HTTP catalog row",
            docs_url="https://docs.example.com/mcp",
            provider_id="locked_mcp",
            connection_spec={
                "kind": "http_none",
                "server_type": "http",
                "auth_type": "NONE",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "server_uri": "https://mcp.example.com/mcp",
            },
            sort_key="0000:locked-http-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)

        catalog_service = PlatformMCPCatalogService(session=session)
        items, _ = await catalog_service.list_catalog(
            workspace_id=integration_service.workspace_id,
            agent_addons_entitled=False,
            q=catalog.slug,
        )

        locked = next(item for item in items if item.slug == catalog.slug)
        assert locked.locked is True
        assert locked.docs_url is None
        assert locked.provider_id is None
        assert locked.connection_spec is None
        assert locked.state == "not_configured"

        existing_mcp = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name=catalog.name,
            slug=catalog.slug,
            catalog_slug=catalog.slug,
            server_type="http",
            server_uri="https://mcp.example.com/mcp",
            auth_type=MCPAuthType.NONE,
        )
        session.add(existing_mcp)
        await session.flush()

        items, _ = await catalog_service.list_catalog(
            workspace_id=integration_service.workspace_id,
            agent_addons_entitled=False,
            q=catalog.slug,
        )

        unlocked = next(item for item in items if item.slug == catalog.slug)
        assert unlocked.locked is False
        assert unlocked.docs_url is None
        assert unlocked.provider_id is None
        assert unlocked.connection_spec is None
        assert unlocked.mcp_integration_id == existing_mcp.id
        assert unlocked.mcp_server_type == "http"
        assert unlocked.mcp_auth_type == MCPAuthType.NONE
        assert unlocked.state == "connected"

    async def test_platform_mcp_catalog_reports_deleted_oauth_row_as_not_connected(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Catalog state is not connected after the workspace MCP row is deleted."""
        oauth_integration = await integration_service.store_integration(
            provider_key=ProviderKey(
                id="custom_mcp_delete_test",
                grant_type=OAuthGrantType.AUTHORIZATION_CODE,
            ),
            access_token=SecretStr("test_access_token"),
            refresh_token=SecretStr("test_refresh_token"),
            expires_in=3600,
        )
        catalog = _catalog_entry(
            slug="oauth-disconnect-mcp",
            name="OAuth Disconnect MCP",
            description="OAuth catalog row",
            connection_spec={
                "kind": "http_oauth2",
                "server_type": "http",
                "auth_type": "OAUTH2",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "server_uri": "https://mcp.example.com/mcp",
                "scopes": [],
                "oauth_authorization_endpoint": None,
                "oauth_token_endpoint": None,
            },
            sort_key="0001:oauth-disconnect-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)
        mcp_integration = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name=catalog.name,
                description=catalog.description,
                catalog_slug=catalog.slug,
                server_uri="https://mcp.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )
        catalog_service = PlatformMCPCatalogService(session=session)

        connected_items, _ = await catalog_service.list_catalog(
            workspace_id=integration_service.workspace_id,
            agent_addons_entitled=True,
            q=catalog.slug,
        )
        connected = next(item for item in connected_items if item.slug == catalog.slug)
        assert connected.state == "connected"
        assert connected.mcp_integration_id == mcp_integration.id
        assert connected.mcp_auth_type == MCPAuthType.OAUTH2

        deleted = await integration_service.delete_mcp_integration(
            mcp_integration_id=mcp_integration.id
        )
        assert deleted is True
        assert await session.get(OAuthIntegration, oauth_integration.id) is None

        disconnected_items, _ = await catalog_service.list_catalog(
            workspace_id=integration_service.workspace_id,
            agent_addons_entitled=True,
            q=catalog.slug,
        )
        item = next(item for item in disconnected_items if item.slug == catalog.slug)
        assert item.state == "not_configured"
        assert item.mcp_integration_id is None
        assert item.mcp_server_type is None
        assert item.mcp_auth_type is None

    async def test_platform_mcp_catalog_state_prefers_connected_row(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With multiple rows for one entry, a connected row beats an older stale one."""
        catalog = _catalog_entry(
            slug="multi-row-mcp",
            name="Multi Row MCP",
            description="Catalog row with several workspace integrations",
            connection_spec={
                "kind": "http_oauth2",
                "server_type": "http",
                "auth_type": "OAUTH2",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "server_uri": "https://mcp.example.com/mcp",
                "scopes": [],
                "oauth_authorization_endpoint": None,
                "oauth_token_endpoint": None,
            },
            sort_key="0001:multi-row-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)

        # Older abandoned OAuth attempt: provider configured, never connected.
        stale_oauth = await integration_service.store_provider_config(
            provider_key=ProviderKey(
                id="custom_mcp_multi_row_stale",
                grant_type=OAuthGrantType.AUTHORIZATION_CODE,
            ),
            client_id="stale-client",
        )
        # Newer attempt that completed the OAuth flow.
        live_oauth = await integration_service.store_integration(
            provider_key=ProviderKey(
                id="custom_mcp_multi_row_live",
                grant_type=OAuthGrantType.AUTHORIZATION_CODE,
            ),
            access_token=SecretStr("live-access-token"),
        )
        now = datetime.now(UTC)
        stale_row = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name="Multi Row MCP",
            slug="multi-row-mcp",
            catalog_slug=catalog.slug,
            server_type="http",
            server_uri="https://mcp.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=stale_oauth.id,
            created_at=now - timedelta(hours=1),
        )
        live_row = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name="Multi Row MCP",
            slug="multi-row-mcp-2",
            catalog_slug=catalog.slug,
            server_type="http",
            server_uri="https://mcp.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=live_oauth.id,
            created_at=now,
        )
        session.add_all([stale_row, live_row])
        await session.commit()

        catalog_service = PlatformMCPCatalogService(session=session)
        items, _ = await catalog_service.list_catalog(
            workspace_id=integration_service.workspace_id,
            agent_addons_entitled=True,
            q=catalog.slug,
        )
        item = next(item for item in items if item.slug == catalog.slug)
        assert item.state == "connected"
        assert item.mcp_integration_id == live_row.id

    async def test_platform_mcp_catalog_connect_requires_entitlement_for_new_rows(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Catalog connect cannot create new platform MCP rows without add-ons."""
        monkeypatch.setattr(
            tier_defaults,
            "DEFAULT_ENTITLEMENTS",
            tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(
                update={"agent_addons": False}
            ),
        )
        catalog = _catalog_entry(
            slug="locked-connect-mcp",
            name="Locked Connect MCP",
            description="Locked connect catalog row",
            connection_spec={
                "kind": "http_none",
                "server_type": "http",
                "auth_type": "NONE",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "server_uri": "https://mcp.example.com/mcp",
            },
            sort_key="0000:locked-connect-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)

        with pytest.raises(EntitlementRequired, match="agent_addons"):
            await integration_service.connect_platform_mcp_catalog(
                catalog_slug=catalog.slug
            )

        with pytest.raises(EntitlementRequired, match="agent_addons"):
            await integration_service.create_mcp_integration(
                params=MCPHttpIntegrationCreate(
                    name="Direct locked MCP",
                    server_uri="https://mcp.example.com/mcp",
                    auth_type=MCPAuthType.NONE,
                    catalog_slug=catalog.slug,
                )
            )

    async def test_create_mcp_integration_rejects_unknown_catalog_slug(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Catalog-backed creates use platform catalog slug as row identity."""
        catalog = _catalog_entry(
            slug="unknown-slug-mcp",
            name="Unknown Slug MCP",
            description="Catalog row",
            connection_spec={
                "kind": "http_none",
                "server_type": "http",
                "auth_type": "NONE",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "server_uri": "https://mcp.example.com/mcp",
            },
            sort_key="0003:unknown-id-mcp",
        )

        with pytest.raises(ValueError, match="Platform MCP catalog row not found"):
            await integration_service.create_mcp_integration(
                params=MCPHttpIntegrationCreate(
                    name=catalog.name,
                    description=catalog.description,
                    catalog_slug="missing-catalog-mcp",
                    server_uri="https://mcp.example.com/mcp",
                    auth_type=MCPAuthType.NONE,
                )
            )

    async def test_create_mcp_integration_rejects_catalog_auth_shape_mismatch(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Creates cannot bind payloads that no catalog connect recipe offers."""
        catalog = _catalog_entry(
            slug="oauth-only-mcp",
            name="OAuth Only MCP",
            description="OAuth-only catalog row",
            connection_spec={
                "kind": "http_oauth2",
                "server_type": "http",
                "auth_type": "OAUTH2",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "server_uri": "https://mcp.example.com/mcp",
                "scopes": [],
                "oauth_authorization_endpoint": None,
                "oauth_token_endpoint": None,
            },
            sort_key="0003:oauth-only-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)

        with pytest.raises(ValueError, match="does not match any connection option"):
            await integration_service.create_mcp_integration(
                params=MCPHttpIntegrationCreate(
                    name=catalog.name,
                    description=catalog.description,
                    catalog_slug=catalog.slug,
                    server_uri="https://mcp.example.com/mcp",
                    auth_type=MCPAuthType.NONE,
                )
            )

    async def test_create_mcp_integration_accepts_matching_connection_option(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Creates may match any of the catalog row's connection options."""
        catalog = _catalog_entry(
            slug="multi-option-mcp",
            name="Multi Option MCP",
            description="Catalog row with OAuth default and custom option",
            connection_spec={
                "kind": "http_oauth2",
                "server_type": "http",
                "auth_type": "OAUTH2",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "server_uri": "https://mcp.example.com/mcp",
                "scopes": [],
                "oauth_authorization_endpoint": None,
                "oauth_token_endpoint": None,
            },
            connection_options=[
                {
                    "id": "custom",
                    "label": "API key",
                    "connection_spec": {
                        "kind": "http_custom",
                        "server_type": "http",
                        "auth_type": "CUSTOM",
                        "requires_config": False,
                        "config_fields": [],
                        "credentials": [],
                        "server_uri": "https://mcp.example.com/mcp",
                    },
                }
            ],
            sort_key="0003:multi-option-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)

        created = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name=catalog.name,
                description=catalog.description,
                catalog_slug=catalog.slug,
                server_uri="https://mcp.example.com/mcp",
                auth_type=MCPAuthType.CUSTOM,
                custom_credentials=SecretStr('{"Authorization": "Bearer test-token"}'),
            )
        )

        assert created.catalog_slug == catalog.slug
        assert created.auth_type == MCPAuthType.CUSTOM

    async def test_create_mcp_integration_rejects_coming_soon_catalog_row(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Creates cannot bind rows that are not yet available to connect."""
        catalog = _catalog_entry(
            slug="coming-soon-mcp",
            name="Coming Soon MCP",
            description="Coming soon catalog row",
            status="coming_soon",
            sort_key="0003:coming-soon-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)

        with pytest.raises(ValueError, match="not available to connect"):
            await integration_service.create_mcp_integration(
                params=MCPHttpIntegrationCreate(
                    name=catalog.name,
                    description=catalog.description,
                    catalog_slug=catalog.slug,
                    server_uri="https://mcp.example.com/mcp",
                    auth_type=MCPAuthType.NONE,
                )
            )

    async def test_byo_mcp_slug_collision_does_not_configure_catalog(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """BYO rows suffix away from exact catalog slugs and stay custom."""
        catalog = _catalog_entry(
            slug="linear-mcp",
            name="Linear MCP",
            description="Linear catalog row",
            connection_spec={
                "kind": "http_none",
                "server_type": "http",
                "auth_type": "NONE",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "server_uri": "https://mcp.linear.app/mcp",
            },
            sort_key="0003:linear-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)

        created = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Linear MCP",
                server_uri="https://linear.example.com/mcp",
                auth_type=MCPAuthType.NONE,
            )
        )

        assert created.slug == "linear-mcp-custom"

        catalog_service = PlatformMCPCatalogService(session=session)
        items, _ = await catalog_service.list_catalog(
            workspace_id=integration_service.workspace_id,
            agent_addons_entitled=True,
            q=catalog.slug,
        )
        item = next(item for item in items if item.slug == catalog.slug)
        assert item.state == "not_configured"
        assert item.mcp_integration_id is None

    async def test_catalog_slug_separates_platform_from_custom_slug_collision(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exact slug collisions do not make custom rows catalog-managed."""
        catalog = _catalog_entry(
            slug="collision-mcp",
            name="Collision MCP",
            description="Collision catalog row",
            connection_spec={
                "kind": "http_none",
                "server_type": "http",
                "auth_type": "NONE",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "server_uri": "https://mcp.example.com/mcp",
            },
            sort_key="0003:collision-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)
        custom = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name="Custom Collision MCP",
            slug=catalog.slug,
            server_type="http",
            server_uri="https://custom.example.com/mcp",
            auth_type=MCPAuthType.NONE,
        )
        session.add(custom)
        await session.flush()

        catalog_service = PlatformMCPCatalogService(session=session)
        items, _ = await catalog_service.list_catalog(
            workspace_id=integration_service.workspace_id,
            agent_addons_entitled=True,
            q=catalog.slug,
        )
        item = next(item for item in items if item.slug == catalog.slug)
        assert item.state == "not_configured"
        assert item.mcp_integration_id is None

        platform_integrations = await integration_service.list_mcp_integrations(
            source="platform"
        )
        workspace_integrations = await integration_service.list_mcp_integrations(
            source="workspace"
        )
        assert custom.id not in {
            integration.id for integration in platform_integrations
        }
        assert custom.id in {integration.id for integration in workspace_integrations}

        result = await integration_service.connect_platform_mcp_catalog(
            catalog_slug=catalog.slug
        )

        created = result.mcp_integration
        assert created is not None
        assert created.id != custom.id
        assert created.slug == f"{catalog.slug}-1"
        assert created.catalog_slug == catalog.slug

        items, _ = await catalog_service.list_catalog(
            workspace_id=integration_service.workspace_id,
            agent_addons_entitled=True,
            q=catalog.slug,
        )
        item = next(item for item in items if item.slug == catalog.slug)
        assert item.state == "connected"
        assert item.mcp_integration_id == created.id

    async def test_connect_platform_mcp_catalog_adopts_legacy_matching_row(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A legacy null-slug row matching the recipe is healed, not duplicated."""
        catalog = _catalog_entry(
            slug="legacy-mcp",
            name="Legacy MCP",
            description="Legacy catalog row",
            connection_spec={
                "kind": "http_none",
                "server_type": "http",
                "auth_type": "NONE",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "server_uri": "https://mcp.example.com/mcp",
            },
            sort_key="0003:legacy-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)
        # Predates catalog_slug, but points at the same host as the recipe.
        legacy = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name="Legacy MCP",
            slug=catalog.slug,
            server_type="http",
            server_uri="https://mcp.example.com/mcp",
            auth_type=MCPAuthType.NONE,
        )
        session.add(legacy)
        await session.flush()
        assert legacy.catalog_slug is None

        result = await integration_service.connect_platform_mcp_catalog(
            catalog_slug=catalog.slug
        )

        adopted = result.mcp_integration
        assert adopted is not None
        # Same row, healed in place — no duplicate created.
        assert adopted.id == legacy.id
        assert adopted.slug == catalog.slug
        assert adopted.catalog_slug == catalog.slug

        catalog_service = PlatformMCPCatalogService(session=session)
        items, _ = await catalog_service.list_catalog(
            workspace_id=integration_service.workspace_id,
            agent_addons_entitled=True,
            q=catalog.slug,
        )
        item = next(item for item in items if item.slug == catalog.slug)
        assert item.mcp_integration_id == legacy.id

    async def test_connect_platform_mcp_catalog_skips_legacy_row_with_other_auth(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A legacy row with a different auth type must not be adopted."""
        catalog = _catalog_entry(
            slug="legacy-auth-mismatch-mcp",
            name="Legacy Auth Mismatch MCP",
            description="Legacy catalog row",
            connection_spec={
                "kind": "http_none",
                "server_type": "http",
                "auth_type": "NONE",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "server_uri": "https://mcp.example.com/mcp",
            },
            sort_key="0003:legacy-auth-mismatch-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)
        # Same slug and host as the recipe, but authenticates differently.
        legacy = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name="Legacy Auth Mismatch MCP",
            slug=catalog.slug,
            server_type="http",
            server_uri="https://mcp.example.com/mcp",
            auth_type=MCPAuthType.CUSTOM,
        )
        session.add(legacy)
        await session.flush()

        result = await integration_service.connect_platform_mcp_catalog(
            catalog_slug=catalog.slug
        )

        created = result.mcp_integration
        assert created is not None
        # A fresh platform row is created; the custom row is left untouched.
        assert created.id != legacy.id
        assert created.catalog_slug == catalog.slug
        assert created.auth_type == MCPAuthType.NONE
        await session.refresh(legacy)
        assert legacy.catalog_slug is None
        assert legacy.auth_type == MCPAuthType.CUSTOM

    async def test_platform_mcp_catalog_existing_row_connects_without_entitlement(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Existing platform MCP rows remain usable without add-ons."""
        monkeypatch.setattr(
            tier_defaults,
            "DEFAULT_ENTITLEMENTS",
            tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(
                update={"agent_addons": False}
            ),
        )
        catalog = _catalog_entry(
            slug="existing-locked-mcp",
            name="Existing Locked MCP",
            description="Existing locked catalog row",
            connection_spec={
                "kind": "http_none",
                "server_type": "http",
                "auth_type": "NONE",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "server_uri": "https://mcp.example.com/mcp",
            },
            sort_key="0000:existing-locked-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)
        existing_mcp = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name=catalog.name,
            slug=catalog.slug,
            catalog_slug=catalog.slug,
            server_type="http",
            server_uri="https://mcp.example.com/mcp",
            auth_type=MCPAuthType.NONE,
        )
        session.add(existing_mcp)
        await session.flush()

        result = await integration_service.connect_platform_mcp_catalog(
            catalog_slug=catalog.slug
        )

        assert result.mcp_integration is not None
        assert result.mcp_integration.id == existing_mcp.id

    async def test_platform_mcp_catalog_oauth_reconnect_requires_entitlement(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Re-auth of a disconnected platform OAuth row is gated like a new connect."""
        monkeypatch.setattr(
            tier_defaults,
            "DEFAULT_ENTITLEMENTS",
            tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(
                update={"agent_addons": False}
            ),
        )
        catalog = _catalog_entry(
            slug="oauth-reconnect-locked-mcp",
            name="OAuth Reconnect Locked MCP",
            description="Disconnected OAuth catalog row",
            connection_spec={
                "kind": "http_oauth2",
                "server_type": "http",
                "auth_type": "OAUTH2",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "server_uri": "https://mcp.example.com/mcp",
                "scopes": [],
                "oauth_authorization_endpoint": None,
                "oauth_token_endpoint": None,
            },
            sort_key="0000:oauth-reconnect-locked-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)
        existing_mcp = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name=catalog.name,
            slug=catalog.slug,
            catalog_slug=catalog.slug,
            server_type="http",
            server_uri="https://mcp.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
        )
        session.add(existing_mcp)
        await session.flush()

        with pytest.raises(EntitlementRequired, match="agent_addons"):
            await integration_service.connect_platform_mcp_catalog(
                catalog_slug=catalog.slug
            )

    async def test_create_custom_provider_avoids_reserved_mcp_prefix(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """User-created providers never land in the custom_mcp_ id namespace."""
        provider = await integration_service.create_custom_provider(
            params=CustomOAuthProviderCreate(
                name="MCP Foo",
                grant_type=OAuthGrantType.AUTHORIZATION_CODE,
                authorization_endpoint="https://auth.example.com/authorize",
                token_endpoint="https://auth.example.com/token",
                client_id="test-client-id",
                client_secret=SecretStr("test-client-secret"),
            )
        )

        assert not integration_service._is_custom_mcp_oauth_provider(
            provider.provider_id
        )

        with pytest.raises(ValueError, match="reserved"):
            await integration_service.create_custom_provider(
                params=CustomOAuthProviderCreate(
                    provider_id="custom_mcp_bar",
                    name="Custom MCP Bar",
                    grant_type=OAuthGrantType.AUTHORIZATION_CODE,
                    authorization_endpoint="https://auth.example.com/authorize",
                    token_endpoint="https://auth.example.com/token",
                    client_id="test-client-id",
                    client_secret=SecretStr("test-client-secret"),
                )
            )

    async def test_mcp_provider_oauth_does_not_auto_create_without_entitlement(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """MCP provider OAuth can store tokens without creating locked MCP rows."""
        monkeypatch.setattr(
            tier_defaults,
            "DEFAULT_ENTITLEMENTS",
            tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(
                update={"agent_addons": False}
            ),
        )
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
        assert auto_created.scalars().first() is None

    async def test_connect_platform_mcp_catalog_creates_default_stdio_row(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Catalog Connect uses the first allowed stdio package option."""
        catalog = _catalog_entry(
            slug="default-stdio-mcp",
            name="Default Stdio MCP",
            description="Default stdio catalog row",
            connection_spec={
                "kind": "stdio_none",
                "server_type": "stdio",
                "auth_type": "NONE",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "stdio_command": None,
                "stdio_args": [],
                "stdio_env": [],
                "packages": [
                    {
                        "manager": "uvx",
                        "command": "uvx",
                        "args": ["example-mcp"],
                        "package": "example-mcp",
                    }
                ],
            },
            sort_key="0001:default-stdio-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)

        result = await integration_service.connect_platform_mcp_catalog(
            catalog_slug=catalog.slug
        )
        created = result.mcp_integration

        assert created is not None
        assert created.slug.startswith(catalog.slug)
        assert created.server_type == "stdio"
        assert created.stdio_command == "uvx"
        assert created.stdio_args == ["example-mcp"]

    async def test_connect_platform_mcp_catalog_requires_config_when_needed(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Catalog Connect does not create rows with missing endpoint values."""
        catalog = _catalog_entry(
            slug="configured-mcp",
            name="Configured MCP",
            description="Configured catalog row",
            connection_spec={
                "kind": "http_custom",
                "server_type": "http",
                "auth_type": "CUSTOM",
                "requires_config": True,
                "config_fields": [
                    {
                        "key": "TENANT",
                        "label": "Tenant",
                        "description": "Workspace tenant",
                        "target": "server_uri",
                        "required": True,
                        "secret": False,
                    }
                ],
                "credentials": [
                    {
                        "key": "TENANT",
                        "label": "Tenant",
                        "description": "Workspace tenant",
                        "target": "server_uri",
                        "required": True,
                        "secret": False,
                    }
                ],
                "server_uri": "https://{TENANT}.example.com/mcp",
            },
            sort_key="0002:configured-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)

        with pytest.raises(ValueError, match="requires configuration"):
            await integration_service.connect_platform_mcp_catalog(
                catalog_slug=catalog.slug
            )

    async def test_connect_platform_mcp_catalog_allows_missing_stdio_env(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Catalog-backed stdio rows can be created before env values exist."""
        catalog = _catalog_entry(
            slug="stdio-env-mcp",
            name="Stdio Env MCP",
            description="Stdio catalog row with required env",
            connection_spec={
                "kind": "stdio_custom",
                "server_type": "stdio",
                "auth_type": "CUSTOM",
                "requires_config": True,
                "config_fields": [
                    {
                        "key": "EXAMPLE_TOKEN",
                        "label": "Example token",
                        "description": "API token",
                        "target": "stdio_env",
                        "required": True,
                        "secret": True,
                    }
                ],
                "credentials": [
                    {
                        "key": "EXAMPLE_TOKEN",
                        "label": "Example token",
                        "description": "API token",
                        "target": "stdio_env",
                        "required": True,
                        "secret": True,
                    }
                ],
                "stdio_command": None,
                "stdio_args": [],
                "stdio_env": [],
                "packages": [
                    {
                        "manager": "uvx",
                        "command": "uvx",
                        "args": ["example-mcp"],
                        "package": "example-mcp",
                    }
                ],
            },
            sort_key="0003:stdio-env-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)

        connected = await integration_service.connect_platform_mcp_catalog(
            catalog_slug=catalog.slug
        )
        assert connected.mcp_integration is not None
        assert connected.mcp_integration.encrypted_stdio_env is None

        created_with_empty_env = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name=f"{catalog.name} Empty",
                description=catalog.description,
                catalog_slug=catalog.slug,
                stdio_command="uvx",
                stdio_args=["example-mcp"],
                stdio_env={"EXAMPLE_TOKEN": ""},
            )
        )
        assert created_with_empty_env.encrypted_stdio_env is not None

        created = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name=catalog.name,
                description=catalog.description,
                catalog_slug=catalog.slug,
                stdio_command="uvx",
                stdio_args=["example-mcp"],
                stdio_env={"EXAMPLE_TOKEN": "token"},
            )
        )

        assert created.slug.startswith(catalog.slug)
        assert created.server_type == "stdio"
        assert created.stdio_command == "uvx"
        assert created.encrypted_stdio_env is not None

        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=created.id,
            params=MCPIntegrationUpdate(stdio_env={"EXAMPLE_TOKEN": ""}),
        )
        assert updated is not None
        assert updated.encrypted_stdio_env is not None

    async def test_connect_platform_mcp_catalog_allows_missing_http_headers(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Catalog Connect can create HTTP rows before headers are filled."""
        catalog = _catalog_entry(
            slug="http-header-mcp",
            name="HTTP Header MCP",
            description="HTTP catalog row with required header",
            connection_spec={
                "kind": "http_custom",
                "server_type": "http",
                "auth_type": "CUSTOM",
                "requires_config": True,
                "config_fields": [
                    {
                        "key": "Authorization",
                        "label": "Authorization",
                        "description": "Bearer token",
                        "target": "http_header",
                        "required": True,
                        "secret": True,
                    }
                ],
                "credentials": [
                    {
                        "key": "Authorization",
                        "label": "Authorization",
                        "description": "Bearer token",
                        "target": "http_header",
                        "required": True,
                        "secret": True,
                    }
                ],
                "server_uri": "https://mcp.example.com/mcp",
            },
            sort_key="0004:http-header-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)

        result = await integration_service.connect_platform_mcp_catalog(
            catalog_slug=catalog.slug
        )

        created = result.mcp_integration
        assert created is not None
        assert created.slug.startswith(catalog.slug)
        assert created.server_type == "http"
        assert created.encrypted_headers is None

    async def test_connect_platform_mcp_catalog_oauth_provider_fallback(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Provider-backed catalog OAuth is a fallback when no spec exists."""
        catalog = _catalog_entry(
            slug="oauth-mcp",
            name="OAuth MCP",
            description="OAuth catalog row",
            provider_id=RunRevealMCPProvider.id,
            sort_key="0003:oauth-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)
        assert integration_service.role.user_id is not None
        user = User(
            id=integration_service.role.user_id,
            email=f"mcp-oauth-{uuid.uuid4()}@example.com",
            hashed_password="test_password",
            is_active=True,
            is_verified=True,
            is_superuser=False,
            last_login_at=None,
        )
        session.add(user)
        await session.flush()

        class FakeProvider:
            id = RunRevealMCPProvider.id
            client_id = "registered-client"
            client_secret = None
            authorization_endpoint = "https://auth.example.com/authorize"
            token_endpoint = "https://auth.example.com/token"
            requested_scopes: list[str] = []

            async def get_authorization_url(self, state: str) -> tuple[str, str]:
                return (
                    f"https://auth.example.com/authorize?state={state}",
                    "pkce-verifier",
                )

        async def _instantiate(cls, *, config=None, **kwargs) -> FakeProvider:
            return FakeProvider()

        monkeypatch.setattr(
            RunRevealMCPProvider,
            "instantiate",
            classmethod(_instantiate),
        )

        result = await integration_service.connect_platform_mcp_catalog(
            catalog_slug=catalog.slug
        )

        assert result.mcp_integration is None
        assert result.oauth_connect is not None
        assert result.oauth_connect.auth_url.startswith(
            "https://auth.example.com/authorize?"
        )
        assert result.oauth_connect.provider_id == RunRevealMCPProvider.id

        mcp_integration = (
            (
                await session.execute(
                    select(MCPIntegration).where(
                        MCPIntegration.workspace_id == integration_service.workspace_id,
                        MCPIntegration.slug == catalog.slug,
                    )
                )
            )
            .scalars()
            .first()
        )
        assert mcp_integration is None

        oauth_state = (
            await session.execute(
                select(OAuthStateDB).where(
                    OAuthStateDB.provider_id == RunRevealMCPProvider.id
                )
            )
        ).scalar_one()
        assert oauth_state.code_verifier == "pkce-verifier"

        provider_config = (
            await session.execute(
                select(OAuthIntegration).where(
                    OAuthIntegration.provider_id == RunRevealMCPProvider.id
                )
            )
        ).scalar_one()
        assert provider_config.encrypted_client_id
        assert provider_config.encrypted_access_token == b""

    async def test_start_existing_custom_mcp_oauth_prefers_stored_endpoints(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Custom MCP providers with stored endpoints must not require discovery.

        Catalog rows that supply static authorization/token endpoints persist
        them on the custom_mcp_* provider config. The authorize flow must build
        the redirect URL from those stored endpoints even when the MCP server
        does not advertise OAuth discovery.
        """
        assert integration_service.role.user_id is not None
        session.add(
            User(
                id=integration_service.role.user_id,
                email=f"mcp-stored-{uuid.uuid4()}@example.com",
                hashed_password="test_password",
                is_active=True,
                is_verified=True,
                is_superuser=False,
                last_login_at=None,
            )
        )
        await session.flush()

        provider_key = ProviderKey(
            id="custom_mcp_stored_endpoints",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )
        integration = await integration_service.store_provider_config(
            provider_key=provider_key,
            client_id="stored-endpoints-client",
            authorization_endpoint="https://accounts.example.test/o/oauth2/authorize",
            token_endpoint="https://oauth2.example.test/token",
        )
        mcp_integration = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name="Stored Endpoints MCP",
            slug="stored-endpoints-mcp",
            server_type="http",
            server_uri="https://mcp.example.test/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=integration.id,
        )
        session.add(mcp_integration)
        await session.commit()

        async def fail_discover(
            *,
            server_uri: str,
        ) -> integration_service_module.MCPOAuthDiscoveryEndpoints:
            raise AssertionError(
                f"discovery must not run for stored endpoints: {server_uri}"
            )

        monkeypatch.setattr(
            integration_service, "_discover_mcp_oauth_endpoints", fail_discover
        )

        result = await integration_service._start_existing_custom_mcp_oauth(
            mcp_integration=mcp_integration
        )

        assert result is not None
        assert result.oauth_connect is not None
        parsed = urlparse(result.oauth_connect.auth_url)
        # The redirect must point at the stored authorization endpoint, and the
        # OAuth `resource` parameter must reflect the MCP server URI.
        assert parsed.scheme == "https"
        assert parsed.hostname == "accounts.example.test"
        assert parsed.path == "/o/oauth2/authorize"
        query = parse_qs(parsed.query)
        assert query["resource"] == ["https://mcp.example.test/mcp"]

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
        resolved = await preset_service.resolve_mcp_integrations([str(created.id)])

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
        resolved = await preset_service.resolve_mcp_integrations([str(created.id)])

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
        resolved = await preset_service.resolve_mcp_integrations([str(created.id)])

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

    async def test_list_mcp_integrations_with_state_uses_oauth_token_state(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """OAuth2 MCP rows without an access token are configured, not connected."""
        configured_oauth = await integration_service.store_provider_config(
            provider_key=ProviderKey(
                id="configured_mcp_state",
                grant_type=OAuthGrantType.AUTHORIZATION_CODE,
            ),
            client_id="configured-client",
            authorization_endpoint="https://auth.example.com/oauth/authorize",
            token_endpoint="https://auth.example.com/oauth/token",
        )
        connected_mcp = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Connected OAuth MCP",
                server_uri="https://connected.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )
        configured_mcp = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Configured OAuth MCP",
                server_uri="https://configured.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=configured_oauth.id,
            )
        )
        none_mcp = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="No Auth MCP",
                server_uri="https://none.example.com/mcp",
                auth_type=MCPAuthType.NONE,
            )
        )

        rows = await integration_service.list_mcp_integrations_with_state()
        state_by_id = {row.integration.id: row.state for row in rows}

        assert state_by_id[connected_mcp.id] == "connected"
        assert state_by_id[configured_mcp.id] == "configured"
        assert state_by_id[none_mcp.id] == "connected"

    async def test_list_mcp_integrations_source_keeps_matching_user_row_as_workspace(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """User-created MCP rows stay workspace-owned even with provider server URI."""
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
        platform_created = auto_created.scalars().first()
        assert platform_created is not None
        server_uri = platform_created.server_uri
        assert server_uri is not None

        workspace_created = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Workspace-authored Provider MCP",
                server_uri=server_uri,
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )

        platform_integrations = await integration_service.list_mcp_integrations(
            source="platform"
        )
        workspace_integrations = await integration_service.list_mcp_integrations(
            source="workspace"
        )

        assert [integration.id for integration in platform_integrations] == [
            platform_created.id
        ]
        assert workspace_created.id in {
            integration.id for integration in workspace_integrations
        }
        assert platform_created.id not in {
            integration.id for integration in workspace_integrations
        }

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
        assert updated.server_uri == created.server_uri  # Unchanged

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

    async def test_update_mcp_integration_switches_stdio_to_http(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test switching an MCP integration from stdio to HTTP in place."""
        created = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Switch MCP",
                stdio_command="npx",
                stdio_args=["@example/server"],
                stdio_env={"EXAMPLE_TOKEN": "secret"},
            )
        )

        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=created.id,
            params=MCPIntegrationUpdate(
                server_type="http",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.NONE,
            ),
        )

        assert updated is not None
        assert updated.id == created.id
        assert updated.server_type == "http"
        assert updated.server_uri == "https://api.example.com/mcp"
        assert updated.auth_type == MCPAuthType.NONE
        assert updated.stdio_command is None
        assert updated.stdio_args is None
        assert updated.encrypted_stdio_env is None

    async def test_update_mcp_integration_switches_http_to_stdio(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test switching an MCP integration from HTTP to stdio in place."""
        created = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Switch MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.CUSTOM,
                custom_credentials=SecretStr('{"Authorization": "Bearer token"}'),
            )
        )
        assert created.encrypted_headers is not None

        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=created.id,
            params=MCPIntegrationUpdate(
                server_type="stdio",
                stdio_command="npx",
                stdio_args=["@example/server"],
                stdio_env={"EXAMPLE_TOKEN": "secret"},
            ),
        )

        assert updated is not None
        assert updated.id == created.id
        assert updated.server_type == "stdio"
        assert updated.server_uri is None
        assert updated.auth_type == MCPAuthType.NONE
        assert updated.oauth_integration_id is None
        assert updated.encrypted_headers is None
        assert updated.stdio_command == "npx"
        assert updated.stdio_args == ["@example/server"]
        assert updated.encrypted_stdio_env is not None

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
        other_mcp_id = uuid.uuid4()
        agent_session = AgentSession(
            workspace_id=integration_service.workspace_id,
            entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
            entity_id=integration_service.workspace_id,
            mcp_integrations=[str(created.id), str(other_mcp_id)],
        )
        preset = AgentPreset(
            workspace_id=integration_service.workspace_id,
            name="Delete MCP preset",
            slug="delete-mcp-preset",
            model_name="gpt-4o-mini",
            model_provider="openai",
            mcp_integrations=[str(created.id)],
        )
        integration_service.session.add(agent_session)
        integration_service.session.add(preset)
        await integration_service.session.flush()
        agent_session_id = agent_session.id
        preset_id = preset.id
        initial_version = AgentPresetVersion(
            workspace_id=integration_service.workspace_id,
            preset_id=preset_id,
            version=1,
            model_name=preset.model_name,
            model_provider=preset.model_provider,
            mcp_integrations=list(preset.mcp_integrations or []),
        )
        integration_service.session.add(initial_version)
        await integration_service.session.flush()
        initial_version_id = initial_version.id
        preset.current_version_id = initial_version_id
        await integration_service.session.commit()

        deleted = await integration_service.delete_mcp_integration(
            mcp_integration_id=created.id
        )

        assert deleted is True

        # Verify it's gone
        retrieved = await integration_service.get_mcp_integration(
            mcp_integration_id=created.id
        )
        assert retrieved is None

        refreshed_preset_result = await integration_service.session.execute(
            select(AgentPreset).where(AgentPreset.id == preset_id)
        )
        refreshed_preset = refreshed_preset_result.scalars().one()
        assert refreshed_preset.current_version_id != initial_version_id
        assert refreshed_preset.mcp_integrations is not None
        assert str(created.id) not in refreshed_preset.mcp_integrations

        refreshed_session_result = await integration_service.session.execute(
            select(AgentSession).where(AgentSession.id == agent_session_id)
        )
        refreshed_session = refreshed_session_result.scalars().one()
        assert refreshed_session.mcp_integrations is not None
        assert str(created.id) not in refreshed_session.mcp_integrations
        assert str(other_mcp_id) in refreshed_session.mcp_integrations

        current_version_result = await integration_service.session.execute(
            select(AgentPresetVersion).where(
                AgentPresetVersion.id == refreshed_preset.current_version_id
            )
        )
        current_version = current_version_result.scalars().one()
        assert current_version.version == 2
        assert current_version.mcp_integrations is not None
        assert str(created.id) not in current_version.mcp_integrations

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

    async def test_delete_mcp_integration_last_reference_deletes_mcp_provider_oauth(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test deleting the last MCP reference deletes MCP-provider OAuth state."""
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

        await integration_service.delete_mcp_integration(
            mcp_integration_id=mcp_integration.id
        )

        refreshed_oauth = await integration_service.session.get(
            OAuthIntegration, oauth_integration.id
        )
        assert refreshed_oauth is None

    async def test_disconnect_mcp_provider_oauth_removes_auto_created_mcp_integration(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test disconnecting MCP-provider OAuth only removes its derived MCP row."""
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
        mcp_integration_id = mcp_integration.id
        server_uri = mcp_integration.server_uri
        assert server_uri is not None

        duplicate_managed_mcp = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name="Duplicate GitHub MCP",
            slug="github_mcp-1",
            server_type="http",
            server_uri=server_uri,
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )
        integration_service.session.add(duplicate_managed_mcp)
        await integration_service.session.flush()
        duplicate_managed_mcp_id = duplicate_managed_mcp.id

        wildcard_collision_mcp = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name="Wildcard collision GitHub MCP",
            slug="github-mcp-1",
            server_type="http",
            server_uri=server_uri,
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )
        integration_service.session.add(wildcard_collision_mcp)
        await integration_service.session.flush()
        wildcard_collision_mcp_id = wildcard_collision_mcp.id

        workspace_created = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Workspace-authored MCP",
                server_uri=server_uri,
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )
        workspace_created_id = workspace_created.id

        preset = AgentPreset(
            workspace_id=integration_service.workspace_id,
            name="MCP provider preset",
            slug="mcp-provider-preset",
            model_name="gpt-4o-mini",
            model_provider="openai",
            mcp_integrations=[
                str(mcp_integration_id),
                str(duplicate_managed_mcp_id),
                str(wildcard_collision_mcp_id),
                str(workspace_created_id),
            ],
        )
        agent_session = AgentSession(
            workspace_id=integration_service.workspace_id,
            entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
            entity_id=integration_service.workspace_id,
            mcp_integrations=[
                str(mcp_integration_id),
                str(duplicate_managed_mcp_id),
                str(wildcard_collision_mcp_id),
                str(workspace_created_id),
            ],
        )
        integration_service.session.add(agent_session)
        integration_service.session.add(preset)
        await integration_service.session.flush()
        agent_session_id = agent_session.id
        preset_id = preset.id
        initial_version = AgentPresetVersion(
            workspace_id=integration_service.workspace_id,
            preset_id=preset_id,
            version=1,
            model_name=preset.model_name,
            model_provider=preset.model_provider,
            mcp_integrations=list(preset.mcp_integrations or []),
        )
        integration_service.session.add(initial_version)
        await integration_service.session.flush()
        initial_version_id = initial_version.id
        preset.current_version_id = initial_version_id
        await integration_service.session.commit()

        await integration_service.disconnect_integration(integration=oauth_integration)

        refreshed_oauth = await integration_service.session.get(
            OAuthIntegration, oauth_integration.id
        )
        assert refreshed_oauth is not None
        assert refreshed_oauth.provider_id == provider_key.id
        assert await integration_service.get_access_token(refreshed_oauth) is None

        deleted_mcp = await integration_service.get_mcp_integration(
            mcp_integration_id=mcp_integration_id
        )
        assert deleted_mcp is None
        deleted_duplicate_mcp = await integration_service.get_mcp_integration(
            mcp_integration_id=duplicate_managed_mcp_id
        )
        assert deleted_duplicate_mcp is None

        wildcard_collision = await integration_service.get_mcp_integration(
            mcp_integration_id=wildcard_collision_mcp_id
        )
        assert wildcard_collision is not None

        surviving_mcp = await integration_service.get_mcp_integration(
            mcp_integration_id=workspace_created_id
        )
        assert surviving_mcp is not None

        refreshed_preset_result = await integration_service.session.execute(
            select(AgentPreset).where(AgentPreset.id == preset_id)
        )
        refreshed_preset = refreshed_preset_result.scalars().first()
        assert refreshed_preset is not None
        assert refreshed_preset.mcp_integrations is not None
        assert str(mcp_integration_id) not in refreshed_preset.mcp_integrations
        assert str(duplicate_managed_mcp_id) not in refreshed_preset.mcp_integrations
        assert str(wildcard_collision_mcp_id) in refreshed_preset.mcp_integrations
        assert str(workspace_created_id) in refreshed_preset.mcp_integrations
        assert refreshed_preset.current_version_id != initial_version_id

        refreshed_session_result = await integration_service.session.execute(
            select(AgentSession).where(AgentSession.id == agent_session_id)
        )
        refreshed_session = refreshed_session_result.scalars().one()
        assert refreshed_session.mcp_integrations is not None
        assert str(mcp_integration_id) not in refreshed_session.mcp_integrations
        assert str(duplicate_managed_mcp_id) not in refreshed_session.mcp_integrations
        assert str(wildcard_collision_mcp_id) in refreshed_session.mcp_integrations
        assert str(workspace_created_id) in refreshed_session.mcp_integrations

        current_version_result = await integration_service.session.execute(
            select(AgentPresetVersion).where(
                AgentPresetVersion.id == refreshed_preset.current_version_id
            )
        )
        current_version = current_version_result.scalars().one()
        assert current_version.version == 2
        assert current_version.mcp_integrations is not None
        assert str(mcp_integration_id) not in current_version.mcp_integrations
        assert str(duplicate_managed_mcp_id) not in current_version.mcp_integrations
        assert str(wildcard_collision_mcp_id) in current_version.mcp_integrations
        assert str(workspace_created_id) in current_version.mcp_integrations

    async def test_disconnect_custom_mcp_oauth_removes_linked_mcp_integrations(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Disconnecting a generic MCP OAuth provider removes linked MCP rows."""
        provider_key = ProviderKey(
            id="custom_mcp_disconnect_test",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )
        oauth_integration = await integration_service.store_integration(
            provider_key=provider_key,
            access_token=SecretStr("test_access_token"),
            refresh_token=SecretStr("test_refresh_token"),
            expires_in=3600,
        )
        created = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Custom MCP OAuth",
                server_uri="https://mcp.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )
        preset = AgentPreset(
            workspace_id=integration_service.workspace_id,
            name="Custom MCP preset",
            slug="custom-mcp-preset",
            model_name="gpt-4o-mini",
            model_provider="openai",
            mcp_integrations=[str(created.id)],
        )
        agent_session = AgentSession(
            workspace_id=integration_service.workspace_id,
            entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
            entity_id=integration_service.workspace_id,
            mcp_integrations=[str(created.id)],
        )
        integration_service.session.add(preset)
        integration_service.session.add(agent_session)
        await integration_service.session.flush()
        preset_id = preset.id
        agent_session_id = agent_session.id
        initial_version = AgentPresetVersion(
            workspace_id=integration_service.workspace_id,
            preset_id=preset_id,
            version=1,
            model_name=preset.model_name,
            model_provider=preset.model_provider,
            mcp_integrations=list(preset.mcp_integrations or []),
        )
        integration_service.session.add(initial_version)
        await integration_service.session.flush()
        initial_version_id = initial_version.id
        preset.current_version_id = initial_version_id
        await integration_service.session.commit()

        await integration_service.disconnect_integration(integration=oauth_integration)

        refreshed_oauth = await integration_service.session.get(
            OAuthIntegration, oauth_integration.id
        )
        assert refreshed_oauth is not None
        assert await integration_service.get_access_token(refreshed_oauth) is None
        assert (
            await integration_service.get_mcp_integration(mcp_integration_id=created.id)
            is None
        )

        refreshed_preset = (
            await integration_service.session.scalars(
                select(AgentPreset).where(AgentPreset.id == preset_id)
            )
        ).one()
        assert refreshed_preset.mcp_integrations is not None
        assert str(created.id) not in refreshed_preset.mcp_integrations
        assert refreshed_preset.current_version_id != initial_version_id

        refreshed_session = (
            await integration_service.session.scalars(
                select(AgentSession).where(AgentSession.id == agent_session_id)
            )
        ).one()
        assert refreshed_session.mcp_integrations is not None
        assert str(created.id) not in refreshed_session.mcp_integrations

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
        agent_session = AgentSession(
            workspace_id=integration_service.workspace_id,
            entity_type=AgentSessionEntity.WORKSPACE_CHAT.value,
            entity_id=integration_service.workspace_id,
            mcp_integrations=[str(created_id)],
        )
        integration_service.session.add(preset)
        integration_service.session.add(agent_session)
        await integration_service.session.commit()
        preset_id = preset.id
        agent_session_id = agent_session.id

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

        refreshed_session_result = await integration_service.session.execute(
            select(AgentSession).where(AgentSession.id == agent_session_id)
        )
        refreshed_session = refreshed_session_result.scalars().first()
        assert refreshed_session is not None
        assert refreshed_session.mcp_integrations is not None
        assert str(created_id) in refreshed_session.mcp_integrations


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
            requested_slug_separator="_",
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

    async def test_resolve_mcp_integration_refs_rejects_now_disallowed_stdio_command(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Regression: resolve_mcp_integration_refs must re-validate stdio command
        policy so rows persisted before rules tightened are rejected at resolution
        time rather than passed through to the agent runtime."""
        created = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Npx MCP",
                stdio_command="npx",
                stdio_args=["@modelcontextprotocol/server-github"],
            )
        )

        preset_service = AgentPresetService(
            session=integration_service.session,
            role=integration_service.role,
        )

        # Simulate tightened policy: remove 'npx' from the allowed set after the
        # row was already persisted.
        from tracecat.exceptions import TracecatValidationError
        from tracecat.integrations import mcp_validation

        original = mcp_validation.ALLOWED_MCP_COMMANDS
        mcp_validation.ALLOWED_MCP_COMMANDS = frozenset(original - {"npx"})
        try:
            # The integration should be skipped; with no remaining refs the
            # method raises TracecatValidationError rather than returning an
            # empty list.
            with pytest.raises(TracecatValidationError):
                await preset_service.resolve_mcp_integration_refs([str(created.id)])
        finally:
            mcp_validation.ALLOWED_MCP_COMMANDS = original


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

    async def test_generic_mcp_discovery_allows_direct_metadata_host_endpoints(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Generic MCP DCR trusts endpoints on the metadata document host."""
        docs = {
            "https://mcp.example.com/.well-known/oauth-protected-resource": None,
            "https://mcp.example.com/.well-known/oauth-protected-resource/mcp": None,
            "https://mcp.example.com/.well-known/oauth-authorization-server": {
                "authorization_endpoint": "https://mcp.example.com/oauth/authorize",
                "token_endpoint": "https://mcp.example.com/oauth/token",
                "registration_endpoint": "https://mcp.example.com/oauth/register",
                "token_endpoint_auth_methods_supported": ["none"],
            },
        }

        async def fake_fetch(url: str) -> OAuthServerMetadata | None:
            return OAuthServerMetadata.from_json(docs[url])

        monkeypatch.setattr(integration_service, "_fetch_oauth_json", fake_fetch)

        endpoints = await integration_service._discover_mcp_oauth_endpoints(
            server_uri="https://mcp.example.com/mcp",
        )

        assert endpoints.authorization_endpoint == (
            "https://mcp.example.com/oauth/authorize"
        )
        assert endpoints.token_endpoint == "https://mcp.example.com/oauth/token"
        assert endpoints.registration_endpoint == (
            "https://mcp.example.com/oauth/register"
        )
        assert endpoints.resource == "https://mcp.example.com/mcp"

    async def test_generic_mcp_discovery_allows_protected_resource_issuer_hosts(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Generic MCP DCR follows authorization_servers from the resource."""
        docs = {
            "https://tenant.example.com/.well-known/oauth-protected-resource": {
                "authorization_servers": ["https://login.example-idp.com"]
            },
            "https://tenant.example.com/.well-known/oauth-protected-resource/mcp": None,
            "https://tenant.example.com/.well-known/oauth-authorization-server": None,
            "https://login.example-idp.com/.well-known/oauth-authorization-server": {
                "authorization_endpoint": (
                    "https://login.example-idp.com/oauth/authorize"
                ),
                "token_endpoint": "https://login.example-idp.com/oauth/token",
                "registration_endpoint": (
                    "https://login.example-idp.com/oauth/register"
                ),
                "token_endpoint_auth_methods_supported": ["client_secret_post"],
            },
        }

        async def fake_fetch(url: str) -> OAuthServerMetadata | None:
            return OAuthServerMetadata.from_json(docs[url])

        monkeypatch.setattr(integration_service, "_fetch_oauth_json", fake_fetch)

        endpoints = await integration_service._discover_mcp_oauth_endpoints(
            server_uri="https://tenant.example.com/mcp",
        )

        assert endpoints.authorization_endpoint == (
            "https://login.example-idp.com/oauth/authorize"
        )
        assert endpoints.token_endpoint == "https://login.example-idp.com/oauth/token"
        assert endpoints.registration_endpoint == (
            "https://login.example-idp.com/oauth/register"
        )
        assert endpoints.token_methods == ["client_secret_post"]
        assert endpoints.resource == "https://tenant.example.com/mcp"

    async def test_generic_mcp_discovery_builds_path_scoped_issuer_metadata_url(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RFC 8414 inserts the well-known path before a path-scoped issuer."""
        docs = {
            "https://tenant.example.com/.well-known/oauth-protected-resource/mcp": {
                "authorization_servers": ["https://login.example-idp.com/tenant"]
            },
            "https://tenant.example.com/.well-known/oauth-protected-resource": None,
            "https://tenant.example.com/.well-known/oauth-authorization-server": None,
            (
                "https://login.example-idp.com"
                "/.well-known/oauth-authorization-server/tenant"
            ): {
                "authorization_endpoint": (
                    "https://login.example-idp.com/tenant/oauth/authorize"
                ),
                "token_endpoint": ("https://login.example-idp.com/tenant/oauth/token"),
                "registration_endpoint": (
                    "https://login.example-idp.com/tenant/oauth/register"
                ),
                "token_endpoint_auth_methods_supported": ["client_secret_post"],
            },
        }

        async def fake_fetch(url: str) -> OAuthServerMetadata | None:
            return OAuthServerMetadata.from_json(docs[url])

        monkeypatch.setattr(integration_service, "_fetch_oauth_json", fake_fetch)

        endpoints = await integration_service._discover_mcp_oauth_endpoints(
            server_uri="https://tenant.example.com/mcp",
        )

        assert endpoints.authorization_endpoint == (
            "https://login.example-idp.com/tenant/oauth/authorize"
        )
        assert endpoints.token_endpoint == (
            "https://login.example-idp.com/tenant/oauth/token"
        )
        assert endpoints.registration_endpoint == (
            "https://login.example-idp.com/tenant/oauth/register"
        )
        assert endpoints.token_methods == ["client_secret_post"]

    async def test_generic_mcp_discovery_preserves_root_trailing_slash_resource(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Generic MCP DCR preserves an explicit root slash in resource URIs."""
        docs = {
            "https://mcp.app.wiz.io/.well-known/oauth-protected-resource": {
                "authorization_endpoint": ("https://mcp.app.wiz.io/oauth/authorize"),
                "token_endpoint": "https://mcp.app.wiz.io/oauth/token",
                "registration_endpoint": "https://mcp.app.wiz.io/oauth/register",
                "token_endpoint_auth_methods_supported": ["none"],
            },
            "https://mcp.app.wiz.io/.well-known/oauth-authorization-server": None,
        }

        async def fake_fetch(url: str) -> OAuthServerMetadata | None:
            return OAuthServerMetadata.from_json(docs[url])

        monkeypatch.setattr(integration_service, "_fetch_oauth_json", fake_fetch)

        endpoints = await integration_service._discover_mcp_oauth_endpoints(
            server_uri="https://mcp.app.wiz.io/",
        )

        assert endpoints.resource == "https://mcp.app.wiz.io/"

    async def test_generic_mcp_discovery_rejects_private_metadata_hosts(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Generic MCP DCR must not fetch private/internal metadata URLs."""
        with pytest.raises(ValueError, match="host is not allowed"):
            await integration_service._fetch_oauth_json(
                "https://127.0.0.1/.well-known/oauth-protected-resource"
            )

    async def test_generic_mcp_discovery_rejects_private_dns_resolution(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Generic MCP DCR must not fetch hosts resolving to private addresses."""

        def fake_getaddrinfo(
            host: str,
            port: int,
            *,
            type: socket.SocketKind,
            proto: int,
        ) -> list[
            tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]
        ]:
            assert host == "metadata.example.test"
            assert type == socket.SOCK_STREAM
            assert proto == socket.IPPROTO_TCP
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.10", port))]

        monkeypatch.setattr(
            "tracecat.integrations.providers.base.socket.getaddrinfo",
            fake_getaddrinfo,
        )

        with pytest.raises(ValueError) as exc:
            await integration_service._fetch_oauth_json(
                "https://metadata.example.test/.well-known/oauth-protected-resource"
            )

        message = str(exc.value)
        assert "host is not allowed" in message
        assert "private" not in message.lower()

    async def test_generic_mcp_callback_rejects_private_token_endpoint_resolution(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Generic MCP OAuth must not POST auth codes to private-resolving hosts."""
        provider_key = ProviderKey(
            id="custom_mcp_private_token_callback",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )
        integration = await integration_service.store_provider_config(
            provider_key=provider_key,
            client_id="private-token-client",
            authorization_endpoint="https://auth.example.test/oauth/authorize",
        )
        session.add(
            MCPIntegration(
                workspace_id=integration_service.workspace_id,
                name="Private Token Callback MCP",
                slug="private-token-callback-mcp",
                server_type="http",
                server_uri="https://mcp.example.test/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=integration.id,
            )
        )
        await session.commit()

        async def fake_discover(
            *,
            server_uri: str,
        ) -> integration_service_module.MCPOAuthDiscoveryEndpoints:
            assert server_uri == "https://mcp.example.test/mcp"
            return integration_service_module.MCPOAuthDiscoveryEndpoints(
                authorization_endpoint="https://auth.example.test/oauth/authorize",
                token_endpoint="https://token.example.test/oauth/token",
                token_methods=["none"],
                registration_endpoint=None,
                resource="https://mcp.example.test/mcp",
            )

        def fake_getaddrinfo(
            host: str,
            port: int,
            *,
            type: socket.SocketKind,
            proto: int,
        ) -> list[
            tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]
        ]:
            assert host == "token.example.test"
            assert type == socket.SOCK_STREAM
            assert proto == socket.IPPROTO_TCP
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.10", port))]

        class FakeOAuthClient:
            def __init__(self, **kwargs: object) -> None:
                _ = kwargs

            async def fetch_token(
                self, *args: object, **kwargs: object
            ) -> dict[str, object]:
                _ = args, kwargs
                raise AssertionError("fetch_token must not be called")

        monkeypatch.setattr(
            integration_service, "_discover_mcp_oauth_endpoints", fake_discover
        )
        monkeypatch.setattr(
            "tracecat.integrations.providers.base.socket.getaddrinfo",
            fake_getaddrinfo,
        )
        monkeypatch.setattr(
            integration_service_module,
            "AsyncOAuth2Client",
            FakeOAuthClient,
        )

        with pytest.raises(ValueError) as exc:
            await integration_service.complete_mcp_oauth_discovery_callback(
                provider_id=provider_key.id,
                code="auth-code",
                state="oauth-state",
                code_verifier="code-verifier",
            )

        message = str(exc.value)
        assert "host is not allowed" in message
        assert "10.0.0.10" not in message
        assert "private" not in message.lower()

    async def test_generic_mcp_callback_uses_registered_token_auth_method(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Generic MCP DCR keeps the assigned token auth method through callback."""
        assert integration_service.role.user_id is not None
        session.add(
            User(
                id=integration_service.role.user_id,
                email=f"mcp-registered-auth-{uuid.uuid4()}@example.com",
                hashed_password="test_password",
                is_active=True,
                is_verified=True,
                is_superuser=False,
                last_login_at=None,
            )
        )
        await session.flush()

        provider_key = ProviderKey(
            id="custom_mcp_registered_auth_method",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )
        oauth_integration = await integration_service.store_provider_config(
            provider_key=provider_key,
            client_id="registered-client",
            client_secret=SecretStr("registered-secret"),
            authorization_endpoint="https://auth.example.test/oauth/authorize",
            token_endpoint="https://auth.example.test/oauth/token",
        )
        session.add(
            MCPIntegration(
                workspace_id=integration_service.workspace_id,
                name="Registered Auth Method MCP",
                slug="registered-auth-method-mcp",
                server_type="http",
                server_uri="https://mcp.example.test/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )
        await session.commit()

        expected_code_verifier: str | None = None

        class FakeOAuthClient:
            init_calls: list[dict[str, object]] = []

            def __init__(self, **kwargs: object) -> None:
                self.init_calls.append(kwargs)

            def create_authorization_url(
                self, authorization_endpoint: str, **kwargs: object
            ) -> tuple[str, str]:
                state = kwargs["state"]
                return f"{authorization_endpoint}?state={state}", str(state)

            async def fetch_token(
                self, *args: object, **kwargs: object
            ) -> dict[str, object]:
                _ = args
                assert kwargs["code_verifier"] == expected_code_verifier
                return {
                    "access_token": "registered-access-token",
                    "refresh_token": "registered-refresh-token",
                    "expires_in": 3600,
                    "scope": "read",
                }

        async def fake_validate_oauth_endpoint(endpoint: str) -> None:
            _ = endpoint

        monkeypatch.setattr(
            integration_service_module,
            "AsyncOAuth2Client",
            FakeOAuthClient,
        )
        monkeypatch.setattr(
            integration_service_module,
            "validate_oauth_endpoint_resolves_public_async",
            fake_validate_oauth_endpoint,
        )

        oauth_connect = await integration_service._start_custom_mcp_oauth_authorization(
            integration=oauth_integration,
            server_uri="https://mcp.example.test/mcp",
            endpoints=integration_service_module.MCPOAuthDiscoveryEndpoints(
                authorization_endpoint="https://auth.example.test/oauth/authorize",
                token_endpoint="https://auth.example.test/oauth/token",
                token_methods=["client_secret_basic", "client_secret_post"],
                registration_endpoint=None,
                resource="https://mcp.example.test/mcp",
            ),
            registration=integration_service_module.MCPOAuthRegistrationResult(
                client_id="registered-client",
                client_secret="registered-secret",
                auth_method="client_secret_post",
            ),
        )
        state_id = uuid.UUID(
            parse_qs(urlparse(oauth_connect.auth_url).query)["state"][0]
        )
        oauth_state = await session.get(OAuthStateDB, state_id)
        assert oauth_state is not None
        callback_state = integration_service._decode_mcp_oauth_callback_state(
            oauth_state.code_verifier
        )
        assert callback_state.token_auth_method == "client_secret_post"
        expected_code_verifier = callback_state.code_verifier

        stored = await integration_service.complete_mcp_oauth_discovery_callback(
            provider_id=provider_key.id,
            code="auth-code",
            state=str(state_id),
            code_verifier=oauth_state.code_verifier,
        )

        assert FakeOAuthClient.init_calls[0]["token_endpoint_auth_method"] == (
            "client_secret_post"
        )
        assert FakeOAuthClient.init_calls[1]["token_endpoint_auth_method"] == (
            "client_secret_post"
        )
        # Persisted so refresh keeps using the registered method.
        assert stored.token_endpoint_auth_method == "client_secret_post"

    async def test_generic_mcp_refresh_uses_persisted_token_auth_method(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Refresh reuses the persisted token auth method over the heuristic pick."""
        provider_key = ProviderKey(
            id="custom_mcp_persisted_auth_method",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )
        integration = await integration_service.store_provider_config(
            provider_key=provider_key,
            client_id="persisted-client",
            client_secret=SecretStr("persisted-secret"),
            authorization_endpoint="https://auth.example.test/oauth/authorize",
        )
        integration.token_endpoint_auth_method = "client_secret_basic"
        session.add(integration)
        session.add(
            MCPIntegration(
                workspace_id=integration_service.workspace_id,
                name="Persisted Auth Method MCP",
                slug="persisted-auth-method-mcp",
                server_type="http",
                server_uri="https://mcp.example.test/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=integration.id,
            )
        )
        await session.commit()

        async def fake_discover(
            *,
            server_uri: str,
        ) -> integration_service_module.MCPOAuthDiscoveryEndpoints:
            assert server_uri == "https://mcp.example.test/mcp"
            return integration_service_module.MCPOAuthDiscoveryEndpoints(
                authorization_endpoint="https://auth.example.test/oauth/authorize",
                token_endpoint="https://auth.example.test/oauth/token",
                # The heuristic alone would pick client_secret_post here.
                token_methods=["client_secret_basic", "client_secret_post"],
                registration_endpoint=None,
                resource="https://mcp.example.test/mcp",
            )

        class FakeOAuthClient:
            init_calls: list[dict[str, object]] = []

            def __init__(self, **kwargs: object) -> None:
                self.init_calls.append(kwargs)

            async def refresh_token(
                self, *args: object, **kwargs: object
            ) -> dict[str, object]:
                _ = args, kwargs
                return {
                    "access_token": "refreshed-access-token",
                    "refresh_token": "refreshed-refresh-token",
                    "expires_in": 3600,
                    "scope": "read",
                }

        async def fake_validate_oauth_endpoint(endpoint: str) -> None:
            _ = endpoint

        monkeypatch.setattr(
            integration_service, "_discover_mcp_oauth_endpoints", fake_discover
        )
        monkeypatch.setattr(
            integration_service_module,
            "AsyncOAuth2Client",
            FakeOAuthClient,
        )
        monkeypatch.setattr(
            integration_service_module,
            "validate_oauth_endpoint_resolves_public_async",
            fake_validate_oauth_endpoint,
        )

        await integration_service._refresh_custom_mcp_integration(
            integration=integration,
            refresh_token="refresh-token",
        )

        assert FakeOAuthClient.init_calls[-1]["token_endpoint_auth_method"] == (
            "client_secret_basic"
        )

    async def test_generic_mcp_refresh_rejects_private_token_endpoint_resolution(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Generic MCP OAuth must not POST refresh tokens to private-resolving hosts."""
        provider_key = ProviderKey(
            id="custom_mcp_private_token_refresh",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )
        integration = await integration_service.store_provider_config(
            provider_key=provider_key,
            client_id="private-token-client",
            authorization_endpoint="https://auth.example.test/oauth/authorize",
        )
        session.add(
            MCPIntegration(
                workspace_id=integration_service.workspace_id,
                name="Private Token Refresh MCP",
                slug="private-token-refresh-mcp",
                server_type="http",
                server_uri="https://mcp.example.test/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=integration.id,
            )
        )
        await session.commit()

        async def fake_discover(
            *,
            server_uri: str,
        ) -> integration_service_module.MCPOAuthDiscoveryEndpoints:
            assert server_uri == "https://mcp.example.test/mcp"
            return integration_service_module.MCPOAuthDiscoveryEndpoints(
                authorization_endpoint="https://auth.example.test/oauth/authorize",
                token_endpoint="https://token.example.test/oauth/token",
                token_methods=["none"],
                registration_endpoint=None,
                resource="https://mcp.example.test/mcp",
            )

        def fake_getaddrinfo(
            host: str,
            port: int,
            *,
            type: socket.SocketKind,
            proto: int,
        ) -> list[
            tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]
        ]:
            assert host == "token.example.test"
            assert type == socket.SOCK_STREAM
            assert proto == socket.IPPROTO_TCP
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.10", port))]

        class FakeOAuthClient:
            def __init__(self, **kwargs: object) -> None:
                _ = kwargs

            async def refresh_token(
                self, *args: object, **kwargs: object
            ) -> dict[str, object]:
                _ = args, kwargs
                raise AssertionError("refresh_token must not be called")

        monkeypatch.setattr(
            integration_service, "_discover_mcp_oauth_endpoints", fake_discover
        )
        monkeypatch.setattr(
            "tracecat.integrations.providers.base.socket.getaddrinfo",
            fake_getaddrinfo,
        )
        monkeypatch.setattr(
            integration_service_module,
            "AsyncOAuth2Client",
            FakeOAuthClient,
        )

        with pytest.raises(ValueError) as exc:
            await integration_service._refresh_custom_mcp_integration(
                integration=integration,
                refresh_token="refresh-token",
            )

        message = str(exc.value)
        assert "host is not allowed" in message
        assert "10.0.0.10" not in message
        assert "private" not in message.lower()

    async def test_generic_mcp_discovery_rejects_untrusted_endpoint_hosts(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Generic MCP DCR endpoints must stay on the metadata document host."""
        docs = {
            "https://mcp.example.com/.well-known/oauth-protected-resource/mcp": None,
            "https://mcp.example.com/.well-known/oauth-protected-resource": None,
            "https://mcp.example.com/.well-known/oauth-authorization-server": {
                "authorization_endpoint": "https://evil.example/oauth/authorize",
                "token_endpoint": "https://evil.example/oauth/token",
                "registration_endpoint": "https://evil.example/oauth/register",
                "token_endpoint_auth_methods_supported": ["none"],
            },
        }

        async def fake_fetch(url: str) -> OAuthServerMetadata | None:
            return OAuthServerMetadata.from_json(docs[url])

        monkeypatch.setattr(integration_service, "_fetch_oauth_json", fake_fetch)

        with pytest.raises(ValueError, match="does not match expected domain"):
            await integration_service._discover_mcp_oauth_endpoints(
                server_uri="https://mcp.example.com/mcp",
            )

    async def test_generic_mcp_discovery_allows_catalog_pinned_endpoint_hosts(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Hosts of catalog-pinned OAuth endpoints are trusted in discovery.

        Mirrors incident.io: metadata on mcp.* advertises authorization/token
        endpoints on app.*, with registration staying on the metadata host.
        """
        docs = {
            "https://mcp.example.com/.well-known/oauth-protected-resource/mcp": None,
            "https://mcp.example.com/.well-known/oauth-protected-resource": None,
            "https://mcp.example.com/.well-known/oauth-authorization-server": {
                "authorization_endpoint": "https://app.example.com/oauth/authorize",
                "token_endpoint": "https://app.example.com/oauth/token",
                "registration_endpoint": "https://mcp.example.com/oauth/register",
                "token_endpoint_auth_methods_supported": ["none"],
            },
        }

        async def fake_fetch(url: str) -> OAuthServerMetadata | None:
            return OAuthServerMetadata.from_json(docs[url])

        monkeypatch.setattr(integration_service, "_fetch_oauth_json", fake_fetch)

        # Unrelated hosts are still rejected even with an allowlist present.
        with pytest.raises(ValueError, match="does not match expected domain"):
            await integration_service._discover_mcp_oauth_endpoints(
                server_uri="https://mcp.example.com/mcp",
                allowed_endpoint_hosts=frozenset({"other.example.com"}),
            )

        endpoints = await integration_service._discover_mcp_oauth_endpoints(
            server_uri="https://mcp.example.com/mcp",
            allowed_endpoint_hosts=frozenset({"app.example.com"}),
        )

        assert endpoints.authorization_endpoint == (
            "https://app.example.com/oauth/authorize"
        )
        assert endpoints.token_endpoint == "https://app.example.com/oauth/token"
        assert endpoints.registration_endpoint == (
            "https://mcp.example.com/oauth/register"
        )

    async def test_connect_mcp_oauth_discovery_trusts_catalog_pinned_hosts(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Connect derives trusted endpoint hosts from catalog-pinned endpoints."""
        captured_hosts: list[frozenset[str]] = []

        async def fake_discover(
            *,
            server_uri: str,
            allowed_endpoint_hosts: frozenset[str] = frozenset(),
        ) -> integration_service_module.MCPOAuthDiscoveryEndpoints:
            _ = server_uri
            captured_hosts.append(allowed_endpoint_hosts)
            raise RuntimeError("stop after capture")

        monkeypatch.setattr(
            integration_service, "_discover_mcp_oauth_endpoints", fake_discover
        )

        catalog_spec = MCPHTTPOAuth2ConnectionSpec(
            server_uri="https://mcp.example.com/mcp",
            oauth_authorization_endpoint="https://app.example.com/oauth/authorize",
            oauth_token_endpoint="https://app.example.com/oauth/token",
        )

        with pytest.raises(RuntimeError, match="stop after capture"):
            await integration_service.connect_mcp_oauth_discovery(
                params=MCPHttpIntegrationCreate(
                    name="Pinned Hosts MCP",
                    server_uri="https://mcp.example.com/mcp",
                    auth_type=MCPAuthType.OAUTH2,
                ),
                catalog_spec=catalog_spec,
            )

        assert captured_hosts == [frozenset({"app.example.com"})]

    async def test_generic_mcp_discovery_uses_protected_resource_identifier(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Generic MCP DCR uses the protected-resource resource value when present."""
        docs = {
            "https://tenant.example.com/.well-known/oauth-protected-resource/mcp": {
                "resource": "https://tenant.example.com/mcp",
                "authorization_servers": ["https://login.example-idp.com"],
            },
            "https://tenant.example.com/.well-known/oauth-protected-resource": None,
            "https://tenant.example.com/.well-known/oauth-authorization-server": None,
            "https://login.example-idp.com/.well-known/oauth-authorization-server": {
                "authorization_endpoint": (
                    "https://login.example-idp.com/oauth/authorize"
                ),
                "token_endpoint": "https://login.example-idp.com/oauth/token",
                "registration_endpoint": "https://login.example-idp.com/oauth/register",
                "token_endpoint_auth_methods_supported": ["none"],
            },
        }

        async def fake_fetch(url: str) -> OAuthServerMetadata | None:
            return OAuthServerMetadata.from_json(docs[url])

        monkeypatch.setattr(integration_service, "_fetch_oauth_json", fake_fetch)

        endpoints = await integration_service._discover_mcp_oauth_endpoints(
            server_uri="https://tenant.example.com/mcp",
        )

        assert endpoints.authorization_endpoint == (
            "https://login.example-idp.com/oauth/authorize"
        )
        assert endpoints.token_endpoint == "https://login.example-idp.com/oauth/token"
        assert endpoints.registration_endpoint == (
            "https://login.example-idp.com/oauth/register"
        )
        assert endpoints.resource == "https://tenant.example.com/mcp"

    def _mock_async_discovery(
        self, monkeypatch: pytest.MonkeyPatch, discovery_doc: dict[str, object]
    ) -> None:
        class FakeAsyncClient:
            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                _ = args

            async def get(self, url: str, *, timeout: float) -> httpx.Response:
                assert timeout == 10.0
                assert url == (
                    "https://api.runreveal.com/.well-known/oauth-authorization-server"
                )
                return httpx.Response(
                    200,
                    json=discovery_doc,
                    request=httpx.Request("GET", url),
                )

        monkeypatch.setattr(
            "tracecat.integrations.providers.base.httpx.AsyncClient",
            FakeAsyncClient,
        )

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

    async def test_mcp_provider_default_resource_uses_full_mcp_uri(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """MCP provider default resource should keep the MCP endpoint path."""

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
        provider = DummyMCPProvider(
            client_id="dummy-client",
            discovered_auth_endpoint="https://dummy.example/oauth/authorize",
            discovered_token_endpoint="https://dummy.example/oauth/token",
        )

        auth_url, _ = await provider.get_authorization_url(state="test-state")
        auth_query = parse_qs(urlparse(auth_url).query)

        assert auth_query["resource"] == ["https://dummy.example/mcp"]
        assert provider._get_additional_token_params()["resource"] == (
            "https://dummy.example/mcp"
        )

    async def test_runreveal_provider_allows_discovered_www_api_oauth_host(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RunReveal serves MCP and OAuth endpoints from separate fixed hosts."""
        discovery_doc = {
            "authorization_endpoint": "https://www-api.runreveal.com/oauth/authorize",
            "token_endpoint": "https://www-api.runreveal.com/oauth/token",
            "registration_endpoint": "https://www-api.runreveal.com/oauth/client",
            "token_endpoint_auth_methods_supported": ["client_secret_post", "none"],
        }
        self._mock_async_discovery(monkeypatch, discovery_doc)

        async def fake_register(
            cls,
            *,
            registration_endpoint: str,
            registration_auth_method: str | None,
            logger_instance,
        ) -> DynamicRegistrationResult:
            _ = cls, logger_instance
            assert registration_endpoint == "https://www-api.runreveal.com/oauth/client"
            assert registration_auth_method == "client_secret_post"
            return DynamicRegistrationResult(
                client_id="runreveal-client",
                client_secret="runreveal-secret",
                auth_method=registration_auth_method,
            )

        monkeypatch.setattr(
            RunRevealMCPProvider,
            "_perform_dynamic_registration_async",
            classmethod(fake_register),
        )

        provider = await RunRevealMCPProvider.instantiate()

        assert provider.client_id == "runreveal-client"
        assert provider.client_secret == "runreveal-secret"
        assert (
            provider.authorization_endpoint
            == "https://www-api.runreveal.com/oauth/authorize"
        )
        assert provider.token_endpoint == "https://www-api.runreveal.com/oauth/token"
        assert provider._registration_endpoint == (
            "https://www-api.runreveal.com/oauth/client"
        )

    async def test_runreveal_provider_rejects_unallowed_discovered_oauth_host(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """RunReveal only allows its exact alternate OAuth host."""
        discovery_doc = {
            "authorization_endpoint": "https://evil.example/oauth/authorize",
            "token_endpoint": "https://evil.example/oauth/token",
            "registration_endpoint": "https://evil.example/oauth/client",
            "token_endpoint_auth_methods_supported": ["none"],
        }
        self._mock_async_discovery(monkeypatch, discovery_doc)

        with pytest.raises(ValueError, match="Could not discover OAuth endpoints"):
            await RunRevealMCPProvider.instantiate()

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
