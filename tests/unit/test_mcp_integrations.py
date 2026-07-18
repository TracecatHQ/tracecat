"""Test suite for MCP integrations.

This test suite covers MCP integration functionality including:
- CRUD operations for all auth types (OAuth2, Custom, None)
- Authentication type switching and credential swapping
- Workspace isolation
- Validation and edge cases
- MCP provider OAuth discovery behavior
"""

import contextlib
import socket
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
from authlib.integrations.base_client.errors import OAuthError
from pydantic import SecretStr, TypeAdapter, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import TerminatedError
from temporalio.service import RPCError, RPCStatusCode

import tracecat.integrations.catalog.service as catalog_service_module
import tracecat.integrations.router as integration_router_module
import tracecat.integrations.service as integration_service_module
from tracecat.agent.mcp.stdio_probe import (
    StdioMCPProbeResult,
    StdioMCPProbeWorkflowInput,
    build_stdio_mcp_probe_workflow_id,
)
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
    Workspace,
)
from tracecat.exceptions import EntitlementRequired
from tracecat.integrations.catalog.loader import catalog_id_for_slug
from tracecat.integrations.catalog.service import PlatformMCPCatalogService
from tracecat.integrations.enums import IntegrationStatus, MCPAuthType, OAuthGrantType
from tracecat.integrations.mcp_validation import (
    MCPConfigurationError,
    MCPConnectionVerificationError,
    MCPSecretResolutionError,
)
from tracecat.integrations.providers.base import (
    DynamicRegistrationResult,
    MCPAuthProvider,
    OAuthDiscoveryResult,
    build_dcr_payload,
    mcp_requested_scopes,
)
from tracecat.integrations.providers.runreveal.mcp import RunRevealMCPProvider
from tracecat.integrations.providers.sentry.mcp import SentryMCPProvider
from tracecat.integrations.providers.wiz.mcp import WizMCPProvider
from tracecat.integrations.schemas import (
    CustomOAuthProviderCreate,
    MCPConnectionOption,
    MCPConnectionSpec,
    MCPHttpIntegrationCreate,
    MCPHttpIntegrationTestConnectionRequest,
    MCPHTTPOAuth2ConnectionSpec,
    MCPIntegrationCreate,
    MCPIntegrationTestConnectionRequest,
    MCPIntegrationTestConnectionResponse,
    MCPIntegrationUpdate,
    MCPStdioIntegrationCreate,
    MCPStdioIntegrationTestConnectionRequest,
    MCPToolPolicyUpdate,
    MCPToolSummary,
    ProviderConfig,
    ProviderKey,
    ProviderMetadata,
    ProviderScopes,
)
from tracecat.integrations.service import (
    IntegrationService,
    OAuthRefreshBusyError,
)
from tracecat.integrations.types import DCRResponse, OAuthServerMetadata
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


async def _noop_validate_oauth_endpoint(endpoint: str) -> None:
    _ = endpoint


def _patch_mcp_dcr_http(
    monkeypatch: pytest.MonkeyPatch,
    response_json: dict[str, object],
    *,
    captured: dict[str, object] | None = None,
) -> None:
    """Stub the DCR POST with a canned response and skip endpoint validation."""

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            _ = args, kwargs

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            _ = args

        async def post(
            self, url: str, *, json: dict[str, object], **kwargs: object
        ) -> httpx.Response:
            _ = kwargs
            if captured is not None:
                captured.update(json)
            return httpx.Response(
                200, json=response_json, request=httpx.Request("POST", url)
            )

    monkeypatch.setattr(
        integration_service_module.httpx, "AsyncClient", FakeAsyncClient
    )
    monkeypatch.setattr(
        integration_service_module,
        "validate_oauth_endpoint_resolves_public_async",
        _noop_validate_oauth_endpoint,
    )


def _patch_mcp_oauth_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    authorize_captured: dict[str, object] | None = None,
    token_response: dict[str, object] | None = None,
    refresh_response: dict[str, object] | None = None,
    init_calls: list[dict[str, object]] | None = None,
) -> None:
    """Stub AsyncOAuth2Client (authorize/fetch/refresh) and skip endpoint validation."""

    class FakeOAuthClient:
        def __init__(self, **kwargs: object) -> None:
            if init_calls is not None:
                init_calls.append(kwargs)

        def create_authorization_url(
            self, authorization_endpoint: str, **kwargs: object
        ) -> tuple[str, str]:
            if authorize_captured is not None:
                authorize_captured.update(kwargs)
            query: dict[str, str] = {"state": str(kwargs["state"])}
            if "scope" in kwargs:
                query["scope"] = str(kwargs["scope"])
            return (
                f"{authorization_endpoint}?{urlencode(query)}",
                str(kwargs["state"]),
            )

        async def fetch_token(
            self, *args: object, **kwargs: object
        ) -> dict[str, object]:
            _ = args, kwargs
            assert token_response is not None, "fetch_token was not stubbed"
            return token_response

        async def refresh_token(
            self, *args: object, **kwargs: object
        ) -> dict[str, object]:
            _ = args, kwargs
            assert refresh_response is not None, "refresh_token was not stubbed"
            return refresh_response

    monkeypatch.setattr(
        integration_service_module, "AsyncOAuth2Client", FakeOAuthClient
    )
    monkeypatch.setattr(
        integration_service_module,
        "validate_oauth_endpoint_resolves_public_async",
        _noop_validate_oauth_endpoint,
    )


def _capture_logger_info(
    monkeypatch: pytest.MonkeyPatch, logger: object
) -> list[tuple[str, dict[str, object]]]:
    logged: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        logger, "info", lambda msg, **kwargs: logged.append((msg, kwargs))
    )
    return logged


async def _seed_service_user(
    session: AsyncSession, integration_service: IntegrationService
) -> None:
    """OAuth authorize flows require the service role's user row to exist."""
    assert integration_service.role.user_id is not None
    session.add(
        User(
            id=integration_service.role.user_id,
            email=f"mcp-user-{uuid.uuid4()}@example.com",
            hashed_password="test_password",
            is_active=True,
            is_verified=True,
            is_superuser=False,
            last_login_at=None,
        )
    )
    await session.flush()


@pytest.fixture
async def integration_service(
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> IntegrationService:
    """Create an integration service instance for testing."""

    @contextlib.asynccontextmanager
    async def get_refresh_session():
        async with AsyncSession(
            session.bind,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as refresh_session:
            yield refresh_session

    monkeypatch.setattr(
        integration_service_module,
        "get_async_session_context_manager",
        get_refresh_session,
    )
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
        assert unlocked.state == "configured"

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
        mcp_integration.tools = []
        session.add(mcp_integration)
        await session.commit()
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
            tools=[],
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

    async def test_platform_mcp_catalog_state_prefers_reauth_over_configured(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A newer configured row must not hide an explicit reconnect state."""
        catalog = _catalog_entry(
            slug="reauth-priority-mcp",
            name="Reauth Priority MCP",
            description="Catalog row with degraded and configured integrations",
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
            sort_key="0001:reauth-priority-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)

        reauth_oauth = await integration_service.store_integration(
            provider_key=ProviderKey(
                id="custom_mcp_reauth_priority",
                grant_type=OAuthGrantType.AUTHORIZATION_CODE,
            ),
            access_token=SecretStr("expired-access-token"),
            expires_in=-60,
        )
        configured_oauth = await integration_service.store_provider_config(
            provider_key=ProviderKey(
                id="custom_mcp_configured_priority",
                grant_type=OAuthGrantType.AUTHORIZATION_CODE,
            ),
            client_id="configured-client",
        )
        now = datetime.now(UTC)
        reauth_row = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name=catalog.name,
            slug=catalog.slug,
            catalog_slug=catalog.slug,
            server_type="http",
            server_uri="https://mcp.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=reauth_oauth.id,
            created_at=now - timedelta(hours=1),
            tools=[],
        )
        configured_row = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name=catalog.name,
            slug=f"{catalog.slug}-2",
            catalog_slug=catalog.slug,
            server_type="http",
            server_uri="https://mcp.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=configured_oauth.id,
            created_at=now,
        )
        session.add_all([reauth_row, configured_row])
        await session.commit()

        catalog_service = PlatformMCPCatalogService(session=session)
        items, _ = await catalog_service.list_catalog(
            workspace_id=integration_service.workspace_id,
            agent_addons_entitled=True,
            q=catalog.slug,
        )

        item = next(item for item in items if item.slug == catalog.slug)
        assert item.state == "reauth_required"
        assert item.mcp_integration_id == reauth_row.id

    async def test_platform_mcp_catalog_ignores_cross_workspace_oauth_state(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Catalog OAuth state must come from the MCP row's workspace."""
        catalog = _catalog_entry(
            slug="cross-workspace-oauth-mcp",
            name="Cross Workspace OAuth MCP",
            description="Catalog row with a stale cross-workspace OAuth reference",
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
            sort_key="0001:cross-workspace-oauth-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)
        assert integration_service.role.organization_id is not None
        foreign_workspace = Workspace(
            name=f"foreign-workspace-{uuid.uuid4()}",
            organization_id=integration_service.role.organization_id,
        )
        session.add(foreign_workspace)
        await session.flush()
        foreign_oauth = OAuthIntegration(
            workspace_id=foreign_workspace.id,
            provider_id="custom_mcp_cross_workspace",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
            encrypted_access_token=b"foreign-access-token",
        )
        local_mcp = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name=catalog.name,
            slug=catalog.slug,
            catalog_slug=catalog.slug,
            server_type="http",
            server_uri="https://mcp.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=foreign_oauth.id,
            tools=[],
        )
        session.add_all([foreign_oauth, local_mcp])
        await session.commit()

        catalog_service = PlatformMCPCatalogService(session=session)
        items, _ = await catalog_service.list_catalog(
            workspace_id=integration_service.workspace_id,
            agent_addons_entitled=True,
            q=catalog.slug,
        )

        item = next(item for item in items if item.slug == catalog.slug)
        assert item.state == "configured"
        assert item.mcp_integration_id == local_mcp.id

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
        assert item.state == "configured"
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

    async def test_catalog_url_typed_stdio_env_requires_scheme(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """stdio_env values for ``type: "url"`` catalog creds need http(s)://."""
        catalog = _catalog_entry(
            slug="url-env-mcp",
            name="URL Env MCP",
            description="Stdio catalog row with a URL-typed env credential",
            connection_spec={
                "kind": "stdio_custom",
                "server_type": "stdio",
                "auth_type": "CUSTOM",
                "requires_config": True,
                "credentials": [
                    {
                        "key": "CONSOLE_BASE_URL",
                        "label": "Console base URL",
                        "description": "Management console endpoint",
                        "target": "stdio_env",
                        "required": True,
                        "secret": False,
                        "type": "url",
                    }
                ],
                "stdio_command": None,
                "stdio_args": [],
                "stdio_env": [],
                "packages": [
                    {
                        "manager": "uvx",
                        "command": "uvx",
                        "args": ["url-mcp"],
                        "package": "url-mcp",
                    }
                ],
            },
            sort_key="0004:url-env-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)

        # Create with a scheme-less URL is rejected.
        with pytest.raises(ValueError, match="http://"):
            await integration_service.create_mcp_integration(
                params=MCPStdioIntegrationCreate(
                    name=catalog.name,
                    description=catalog.description,
                    catalog_slug=catalog.slug,
                    stdio_command="uvx",
                    stdio_args=["url-mcp"],
                    stdio_env={"CONSOLE_BASE_URL": "console.example.net"},
                )
            )

        # Create with a valid https URL succeeds.
        created = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name=catalog.name,
                description=catalog.description,
                catalog_slug=catalog.slug,
                stdio_command="uvx",
                stdio_args=["url-mcp"],
                stdio_env={"CONSOLE_BASE_URL": "https://console.example.net"},
            )
        )
        assert created.encrypted_stdio_env is not None

        # Update to a scheme-less URL is rejected against the bound catalog row.
        with pytest.raises(ValueError, match="http://"):
            await integration_service.update_mcp_integration(
                mcp_integration_id=created.id,
                params=MCPIntegrationUpdate(
                    stdio_env={"CONSOLE_BASE_URL": "console.example.net"}
                ),
            )

    async def test_catalog_url_typed_stdio_env_from_connection_option_on_update(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Update validates URL keys declared by a non-default connection option.

        Mirrors rows whose default option is remote HTTP while a local-stdio
        option carries a ``type: "url"`` env var (e.g. Panther/Jamf). The
        URL-typed key lives only in ``connection_options``, not the top-level
        ``connection_spec``, so update-time validation must union both.
        """
        catalog = _catalog_entry(
            slug="multi-option-url-env-mcp",
            name="Multi Option URL Env MCP",
            description="Remote HTTP default with a local-stdio URL env option",
            connection_spec={
                "kind": "http_custom",
                "server_type": "http",
                "auth_type": "CUSTOM",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "server_uri": "https://mcp.example.com/mcp",
            },
            connection_options=[
                {
                    "id": "local-stdio",
                    "label": "Local (stdio)",
                    "connection_spec": {
                        "kind": "stdio_custom",
                        "server_type": "stdio",
                        "auth_type": "CUSTOM",
                        "requires_config": True,
                        "credentials": [
                            {
                                "key": "CONSOLE_BASE_URL",
                                "label": "Console base URL",
                                "description": "Management console endpoint",
                                "target": "stdio_env",
                                "required": True,
                                "secret": False,
                                "type": "url",
                            }
                        ],
                        "stdio_command": None,
                        "stdio_args": [],
                        "stdio_env": [],
                        "packages": [
                            {
                                "manager": "uvx",
                                "command": "uvx",
                                "args": ["url-mcp"],
                                "package": "url-mcp",
                            }
                        ],
                    },
                }
            ],
            sort_key="0005:multi-option-url-env-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)

        created = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name=catalog.name,
                description=catalog.description,
                catalog_slug=catalog.slug,
                stdio_command="uvx",
                stdio_args=["url-mcp"],
                stdio_env={"CONSOLE_BASE_URL": "https://console.example.net"},
            )
        )

        # Update to a scheme-less URL is rejected even though the URL-typed key
        # is declared only by the connection option, not connection_spec.
        with pytest.raises(ValueError, match="http://"):
            await integration_service.update_mcp_integration(
                mcp_integration_id=created.id,
                params=MCPIntegrationUpdate(
                    stdio_env={"CONSOLE_BASE_URL": "console.example.net"}
                ),
            )

        # A valid https URL still updates cleanly.
        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=created.id,
            params=MCPIntegrationUpdate(
                stdio_env={"CONSOLE_BASE_URL": "https://console.example.org"}
            ),
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

    async def _run_reconnect_scope_case(
        self,
        *,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        provider_id: str,
        stored_scopes: list[str] | None,
        advertised_scopes: list[str],
    ) -> dict[str, object]:
        """Reconnect an existing custom MCP row and return authorize kwargs.

        Only the authorization endpoint is stored so endpoint resolution falls
        through to discovery, letting ``advertised_scopes`` drive the flow.
        """
        await _seed_service_user(session, integration_service)

        provider_key = ProviderKey(
            id=provider_id, grant_type=OAuthGrantType.AUTHORIZATION_CODE
        )
        oauth_integration = await integration_service.store_provider_config(
            provider_key=provider_key,
            client_id="reconnect-client",
            authorization_endpoint="https://auth.example.test/oauth/authorize",
            requested_scopes=stored_scopes,
        )
        mcp_integration = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name="Reconnect Scope MCP",
            slug=f"reconnect-scope-mcp-{uuid.uuid4().hex[:8]}",
            server_type="http",
            server_uri="https://mcp.example.test/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )
        session.add(mcp_integration)
        await session.commit()

        async def fake_discover(
            *,
            server_uri: str,
        ) -> integration_service_module.MCPOAuthDiscoveryEndpoints:
            assert server_uri == "https://mcp.example.test/mcp"
            return integration_service_module.MCPOAuthDiscoveryEndpoints(
                authorization_endpoint="https://auth.example.test/oauth/authorize",
                token_endpoint="https://auth.example.test/oauth/token",
                token_methods=["none"],
                scopes_supported=advertised_scopes,
                registration_endpoint=None,
                resource="https://mcp.example.test/mcp",
            )

        monkeypatch.setattr(
            integration_service, "_discover_mcp_oauth_endpoints", fake_discover
        )
        captured: dict[str, object] = {}
        _patch_mcp_oauth_client(monkeypatch, authorize_captured=captured)

        result = await integration_service._start_existing_custom_mcp_oauth(
            mcp_integration=mcp_integration
        )
        assert result is not None
        assert result.oauth_connect is not None
        return captured

    async def test_reconnect_narrowed_scopes_not_re_expanded(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A DCR-narrowed stored set is sent verbatim, without re-adding offline_access."""
        captured = await self._run_reconnect_scope_case(
            integration_service=integration_service,
            session=session,
            monkeypatch=monkeypatch,
            provider_id="custom_mcp_reconnect_narrowed",
            stored_scopes=["mcp:read"],
            advertised_scopes=["mcp:read", "offline_access"],
        )
        assert captured["scope"] == "mcp:read"

    async def test_reconnect_legacy_null_scopes_expand_with_offline_access(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Legacy rows with NULL requested_scopes still expand on reconnect."""
        captured = await self._run_reconnect_scope_case(
            integration_service=integration_service,
            session=session,
            monkeypatch=monkeypatch,
            provider_id="custom_mcp_reconnect_legacy",
            stored_scopes=None,
            advertised_scopes=["offline_access"],
        )
        assert captured["scope"] == "offline_access"

    async def test_reconnect_stored_offline_access_sent_unchanged(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A stored set already carrying offline_access is not duplicated."""
        captured = await self._run_reconnect_scope_case(
            integration_service=integration_service,
            session=session,
            monkeypatch=monkeypatch,
            provider_id="custom_mcp_reconnect_stored_offline",
            stored_scopes=["mcp:read", "offline_access"],
            advertised_scopes=["mcp:read", "offline_access"],
        )
        assert captured["scope"] == "mcp:read offline_access"

    async def test_reconnect_empty_stored_scopes_stay_empty(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An explicit-empty stored set stays empty and omits the scope param."""
        captured = await self._run_reconnect_scope_case(
            integration_service=integration_service,
            session=session,
            monkeypatch=monkeypatch,
            provider_id="custom_mcp_reconnect_empty",
            stored_scopes=[],
            advertised_scopes=["offline_access"],
        )
        assert "scope" not in captured

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
        """OAuth2 MCP state accounts for token presence and grant behavior."""
        configured_oauth = await integration_service.store_provider_config(
            provider_key=ProviderKey(
                id="configured_mcp_state",
                grant_type=OAuthGrantType.AUTHORIZATION_CODE,
            ),
            client_id="configured-client",
            authorization_endpoint="https://auth.example.com/oauth/authorize",
            token_endpoint="https://auth.example.com/oauth/token",
        )
        client_credentials_oauth = OAuthIntegration(
            workspace_id=integration_service.workspace_id,
            provider_id="client_credentials_mcp_state",
            grant_type=OAuthGrantType.CLIENT_CREDENTIALS,
            encrypted_access_token=b"expired-access-token",
            encrypted_client_id=b"client-id",
            encrypted_client_secret=b"client-secret",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        integration_service.session.add(client_credentials_oauth)
        await integration_service.session.flush()
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
        client_credentials_mcp = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Client Credentials OAuth MCP",
                server_uri="https://client-credentials.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=client_credentials_oauth.id,
            )
        )
        none_mcp = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="No Auth MCP",
                server_uri="https://none.example.com/mcp",
                auth_type=MCPAuthType.NONE,
            )
        )
        connected_mcp.tools = []
        configured_mcp.tools = []
        client_credentials_mcp.tools = []
        none_mcp.tools = []
        integration_service.session.add_all(
            [
                connected_mcp,
                configured_mcp,
                client_credentials_mcp,
                none_mcp,
            ]
        )
        await integration_service.session.commit()

        rows = await integration_service.list_mcp_integrations_with_state()
        state_by_id = {row.integration.id: row.state for row in rows}

        assert state_by_id[connected_mcp.id] == "connected"
        assert state_by_id[configured_mcp.id] == "configured"
        assert state_by_id[client_credentials_mcp.id] == "connected"
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

    async def test_update_http_to_stdio_verification_does_not_merge_http_tools(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """HTTP tool snapshots are discarded when switching to stdio."""
        http_integration = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="HTTP To Stdio MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.NONE,
            )
        )
        http_integration.tools = [
            MCPToolSummary(name="http_tool", description="HTTP").model_dump()
        ]
        integration_service.session.add(http_integration)
        await integration_service.session.commit()

        async def _probe_stdio(
            mcp_integration: MCPIntegration,
        ) -> list[MCPToolSummary]:
            assert mcp_integration.id == http_integration.id
            assert mcp_integration.server_type == "stdio"
            assert mcp_integration.tools is None
            return [MCPToolSummary(name="stdio_tool", description="Stdio")]

        monkeypatch.setattr(
            integration_service,
            "_probe_mcp_stdio_server",
            _probe_stdio,
        )

        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=http_integration.id,
            params=MCPIntegrationUpdate(
                server_type="stdio",
                stdio_command="npx",
                stdio_args=["@example/server"],
            ),
            verify_connection=True,
        )

        assert updated is not None
        tools = MCPToolSummary.validate_stored(updated.tools)
        assert tools is not None
        assert [tool.name for tool in tools] == ["stdio_tool"]

    async def test_update_stdio_to_http_verification_does_not_merge_stdio_tools(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Stdio tool snapshots are discarded when switching to HTTP."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Stdio To HTTP MCP",
                stdio_command="npx",
                stdio_args=["@example/server"],
            )
        )
        stdio_integration.tools = [
            MCPToolSummary(name="stdio_tool", description="Stdio").model_dump()
        ]
        integration_service.session.add(stdio_integration)
        await integration_service.session.commit()

        async def _probe_http(
            mcp_integration: MCPIntegration,
        ) -> list[MCPToolSummary]:
            assert mcp_integration.id == stdio_integration.id
            assert mcp_integration.server_type == "http"
            assert mcp_integration.server_uri == "https://api.example.com/mcp"
            return [MCPToolSummary(name="http_tool", description="HTTP")]

        monkeypatch.setattr(
            integration_service,
            "_probe_mcp_http_server",
            _probe_http,
        )

        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=stdio_integration.id,
            params=MCPIntegrationUpdate(
                server_type="http",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.NONE,
            ),
            verify_connection=True,
        )

        assert updated is not None
        tools = MCPToolSummary.validate_stored(updated.tools)
        assert tools is not None
        assert [tool.name for tool in tools] == ["http_tool"]

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

    async def test_resolve_mcp_integration_refs_includes_verified_stdio_tools(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Stdio refs carry non-secret verified tools for runtime inventory."""
        created = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="SentinelOne",
                stdio_command="uvx",
                stdio_args=["sentinelone-mcp"],
            )
        )
        created.tools = [
            MCPToolSummary(
                name="list_alerts",
                description="List alerts",
            ).model_dump(),
            MCPToolSummary(
                name="delete_alert",
                description="Delete alert",
                enabled=False,
            ).model_dump(),
            MCPToolSummary(
                name="legacy_alert",
                description="Legacy alert",
                status="missing",
            ).model_dump(),
        ]
        await integration_service.session.commit()

        preset_service = AgentPresetService(
            session=integration_service.session,
            role=integration_service.role,
        )

        resolved = await preset_service.resolve_mcp_integration_refs([str(created.id)])

        assert resolved == [
            {
                "type": "stdio",
                "name": created.slug,
                "command": "uvx",
                "args": ["sentinelone-mcp"],
                "id": str(created.id),
                "timeout": 30,
                "tools": [
                    {
                        "name": "list_alerts",
                        "description": "List alerts",
                        "enabled": True,
                        "requires_approval": False,
                        "status": "available",
                    }
                ],
            }
        ]


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

    async def test_generic_mcp_discovery_captures_scopes_supported(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Discovery carries the AS metadata scopes_supported onto endpoints."""
        docs = {
            "https://mcp.example.com/.well-known/oauth-protected-resource": None,
            "https://mcp.example.com/.well-known/oauth-protected-resource/mcp": None,
            "https://mcp.example.com/.well-known/oauth-authorization-server": {
                "authorization_endpoint": "https://mcp.example.com/oauth/authorize",
                "token_endpoint": "https://mcp.example.com/oauth/token",
                "registration_endpoint": "https://mcp.example.com/oauth/register",
                "token_endpoint_auth_methods_supported": ["none"],
                "scopes_supported": ["read", "offline_access"],
            },
        }

        async def fake_fetch(url: str) -> OAuthServerMetadata | None:
            return OAuthServerMetadata.from_json(docs[url])

        monkeypatch.setattr(integration_service, "_fetch_oauth_json", fake_fetch)

        endpoints = await integration_service._discover_mcp_oauth_endpoints(
            server_uri="https://mcp.example.com/mcp",
        )

        assert endpoints.scopes_supported == ["read", "offline_access"]

    def test_oauth_server_metadata_rejects_malformed_string_lists(self) -> None:
        """Known metadata fields must retain their declared wire types."""
        with pytest.raises(ValidationError):
            OAuthServerMetadata.from_json({"scopes_supported": ["read", 42]})

    @pytest.mark.parametrize(
        "grant_types",
        ["authorization_code", ["authorization_code", 42]],
    )
    def test_dcr_response_rejects_malformed_grant_types(
        self, grant_types: object
    ) -> None:
        """Malformed DCR grants cannot masquerade as omitted or narrowed grants."""
        with pytest.raises(ValidationError):
            DCRResponse.model_validate(
                {"client_id": "dcr-client", "grant_types": grant_types}
            )

    @pytest.mark.parametrize("scope", [42, ["read"]])
    def test_dcr_response_rejects_malformed_scope(self, scope: object) -> None:
        """Malformed DCR scope echoes cannot masquerade as an omitted echo."""
        with pytest.raises(ValidationError):
            DCRResponse.model_validate({"client_id": "dcr-client", "scope": scope})

    async def test_resolve_mcp_static_endpoints_have_empty_scopes_supported(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """The static-endpoint branch does not discover scopes_supported."""
        provider_config = integration_service_module.ProviderConfig(
            authorization_endpoint="https://auth.example.test/oauth/authorize",
            token_endpoint="https://auth.example.test/oauth/token",
        )

        endpoints = await integration_service._resolve_mcp_oauth_endpoints(
            server_uri="https://mcp.example.test/mcp",
            provider_config=provider_config,
        )

        assert endpoints.scopes_supported == []

    def test_mcp_requested_scopes_adds_offline_access_when_advertised(self) -> None:
        """offline_access is added only when the AS advertises it, without dupes."""
        assert mcp_requested_scopes(
            scopes=["read"], scopes_supported=["read", "offline_access"]
        ) == ["read", "offline_access"]
        assert mcp_requested_scopes(scopes=["read"], scopes_supported=["read"]) == [
            "read"
        ]
        assert mcp_requested_scopes(
            scopes=None, scopes_supported=["offline_access"]
        ) == ["offline_access"]
        # No duplicate when already present.
        assert mcp_requested_scopes(
            scopes=["offline_access"], scopes_supported=["offline_access"]
        ) == ["offline_access"]
        # Empty when nothing configured and nothing advertised.
        assert mcp_requested_scopes(scopes=None, scopes_supported=[]) == []
        # An explicit empty grant (e.g. narrowed by a DCR echo) stays empty
        # even when offline_access is advertised.
        assert (
            mcp_requested_scopes(scopes=[], scopes_supported=["offline_access"]) == []
        )

    async def test_mcp_dcr_payload_advertises_refresh_token_grant(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Custom MCP DCR requests both grant types and a scope whitelist."""
        captured: dict[str, object] = {}
        _patch_mcp_dcr_http(
            monkeypatch,
            {"client_id": "dcr-client", "client_secret": None},
            captured=captured,
        )

        result = await integration_service._perform_mcp_dynamic_registration(
            registration_endpoint="https://auth.example.test/oauth/register",
            client_name="Test MCP",
            token_auth_method="none",
            requested_scopes=["read", "offline_access"],
        )

        assert result.client_id == "dcr-client"
        assert captured["grant_types"] == ["authorization_code", "refresh_token"]
        assert captured["scope"] == "read offline_access"

    async def test_mcp_dcr_payload_omits_scope_when_empty(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Custom MCP DCR omits the scope key when nothing is requested."""
        captured: dict[str, object] = {}
        _patch_mcp_dcr_http(monkeypatch, {"client_id": "dcr-client"}, captured=captured)

        await integration_service._perform_mcp_dynamic_registration(
            registration_endpoint="https://auth.example.test/oauth/register",
            client_name="Test MCP",
            token_auth_method=None,
            requested_scopes=[],
        )

        assert "scope" not in captured
        assert captured["grant_types"] == ["authorization_code", "refresh_token"]

    def test_build_dcr_payload_advertises_refresh_token_grant(self) -> None:
        """The shared provider-class DCR builder advertises refresh_token too."""
        payload = build_dcr_payload(
            client_name="Provider",
            redirect_uris=["https://app.test/callback"],
        )
        assert payload["grant_types"] == ["authorization_code", "refresh_token"]
        assert "token_endpoint_auth_method" not in payload
        assert "scope" not in payload
        payload_with_method = build_dcr_payload(
            client_name="Provider",
            redirect_uris=["https://app.test/callback"],
            token_endpoint_auth_method="none",
            requested_scopes=["read", "offline_access"],
        )
        assert payload_with_method["token_endpoint_auth_method"] == "none"
        assert payload_with_method["scope"] == "read offline_access"

    @pytest.mark.parametrize(
        ("response_json", "expected_downgraded", "expected_grant_types"),
        [
            # AS dropped refresh_token from what we requested.
            (
                {
                    "client_id": "dcr-client",
                    "grant_types": ["authorization_code"],
                    "scope": "read",
                },
                True,
                ["authorization_code"],
            ),
            # AS echoed both grants back.
            (
                {
                    "client_id": "dcr-client",
                    "grant_types": ["authorization_code", "refresh_token"],
                },
                False,
                ["authorization_code", "refresh_token"],
            ),
            # AS declared grant_types empty: all grants stripped, downgrade.
            (
                {"client_id": "dcr-client", "grant_types": []},
                True,
                [],
            ),
            # AS omitted grant_types entirely: parsed None, no downgrade inferred.
            ({"client_id": "dcr-client"}, False, None),
        ],
    )
    async def test_mcp_dcr_logs_registered_grant_types_and_downgrade(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
        response_json: dict[str, object],
        expected_downgraded: bool,
        expected_grant_types: list[str] | None,
    ) -> None:
        """Registration success log reports echoed metadata and downgrade flag."""
        _patch_mcp_dcr_http(monkeypatch, response_json)
        logged = _capture_logger_info(monkeypatch, integration_service.logger)

        await integration_service._perform_mcp_dynamic_registration(
            registration_endpoint="https://auth.example.test/oauth/register",
            client_name="Test MCP",
            token_auth_method="none",
            requested_scopes=["read", "offline_access"],
        )

        reg_logs = [
            kw for msg, kw in logged if msg == "Registered custom MCP OAuth client"
        ]
        assert len(reg_logs) == 1
        assert reg_logs[0]["registration_endpoint_host"] == "auth.example.test"
        assert reg_logs[0]["registered_grant_types"] == expected_grant_types
        assert reg_logs[0]["grant_types_downgraded"] is expected_downgraded
        assert reg_logs[0]["registered_scope"] == response_json.get("scope")
        # Secrets must never appear in the log.
        assert "client_id" not in reg_logs[0]
        assert "client_secret" not in reg_logs[0]

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

    async def _run_authorize_scope_case(
        self,
        *,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        provider_id: str,
        requested_scopes: list[str],
    ) -> dict[str, object]:
        """Drive authorize and return the create_authorization_url kwargs."""
        await _seed_service_user(session, integration_service)

        provider_key = ProviderKey(
            id=provider_id, grant_type=OAuthGrantType.AUTHORIZATION_CODE
        )
        oauth_integration = await integration_service.store_provider_config(
            provider_key=provider_key,
            client_id="scope-client",
            authorization_endpoint="https://auth.example.test/oauth/authorize",
            token_endpoint="https://auth.example.test/oauth/token",
        )
        await session.commit()

        captured: dict[str, object] = {}
        _patch_mcp_oauth_client(monkeypatch, authorize_captured=captured)

        await integration_service._start_custom_mcp_oauth_authorization(
            integration=oauth_integration,
            server_uri="https://mcp.example.test/mcp",
            endpoints=integration_service_module.MCPOAuthDiscoveryEndpoints(
                authorization_endpoint="https://auth.example.test/oauth/authorize",
                token_endpoint="https://auth.example.test/oauth/token",
                token_methods=["none"],
                registration_endpoint=None,
                resource="https://mcp.example.test/mcp",
            ),
            registration=integration_service_module.MCPOAuthRegistrationResult(
                client_id="scope-client",
                client_secret=None,
                auth_method=None,
            ),
            requested_scopes=requested_scopes,
        )
        return captured

    async def test_authorize_url_includes_offline_access_when_advertised(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The authorize URL carries the requested scopes when non-empty."""
        captured = await self._run_authorize_scope_case(
            integration_service=integration_service,
            session=session,
            monkeypatch=monkeypatch,
            provider_id="custom_mcp_scope_offline",
            requested_scopes=["read", "offline_access"],
        )
        assert captured["scope"] == "read offline_access"

    async def test_authorize_url_omits_scope_when_nothing_requested(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No scope param is sent when there is nothing to request."""
        captured = await self._run_authorize_scope_case(
            integration_service=integration_service,
            session=session,
            monkeypatch=monkeypatch,
            provider_id="custom_mcp_scope_empty",
            requested_scopes=[],
        )
        assert "scope" not in captured

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
            requested_scopes=[],
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

    async def test_generic_mcp_callback_logs_granted_scope(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The code-exchange log reports the granted scope from the token response."""
        await _seed_service_user(session, integration_service)

        provider_key = ProviderKey(
            id="custom_mcp_callback_scope",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )
        oauth_integration = await integration_service.store_provider_config(
            provider_key=provider_key,
            client_id="callback-scope-client",
            authorization_endpoint="https://auth.example.test/oauth/authorize",
            token_endpoint="https://auth.example.test/oauth/token",
        )
        session.add(
            MCPIntegration(
                workspace_id=integration_service.workspace_id,
                name="Callback Scope MCP",
                slug="callback-scope-mcp",
                server_type="http",
                server_uri="https://mcp.example.test/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )
        await session.commit()

        _patch_mcp_oauth_client(
            monkeypatch,
            token_response={
                "access_token": "callback-access-token",
                "refresh_token": "callback-refresh-token",
                "expires_in": 3600,
                "scope": "read offline_access",
            },
        )

        oauth_connect = await integration_service._start_custom_mcp_oauth_authorization(
            integration=oauth_integration,
            server_uri="https://mcp.example.test/mcp",
            endpoints=integration_service_module.MCPOAuthDiscoveryEndpoints(
                authorization_endpoint="https://auth.example.test/oauth/authorize",
                token_endpoint="https://auth.example.test/oauth/token",
                token_methods=["none"],
                registration_endpoint=None,
                resource="https://mcp.example.test/mcp",
            ),
            registration=integration_service_module.MCPOAuthRegistrationResult(
                client_id="callback-scope-client",
                client_secret=None,
                auth_method=None,
            ),
            requested_scopes=["read", "offline_access"],
        )
        state_id = uuid.UUID(
            parse_qs(urlparse(oauth_connect.auth_url).query)["state"][0]
        )
        oauth_state = await session.get(OAuthStateDB, state_id)
        assert oauth_state is not None

        logged = _capture_logger_info(monkeypatch, integration_service.logger)

        await integration_service.complete_mcp_oauth_discovery_callback(
            provider_id=provider_key.id,
            code="auth-code",
            state=str(state_id),
            code_verifier=oauth_state.code_verifier,
        )

        callback_logs = [
            kw
            for msg, kw in logged
            if msg == "Completed custom MCP OAuth authorization"
        ]
        assert len(callback_logs) == 1
        assert callback_logs[0]["granted_scope"] == "read offline_access"
        assert callback_logs[0]["has_refresh_token"] is True
        assert callback_logs[0]["expires_in"] == 3600

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

        init_calls: list[dict[str, object]] = []
        _patch_mcp_oauth_client(
            monkeypatch,
            refresh_response={
                "access_token": "refreshed-access-token",
                "refresh_token": "refreshed-refresh-token",
                "expires_in": 3600,
                "scope": "read",
            },
            init_calls=init_calls,
        )
        monkeypatch.setattr(
            integration_service, "_discover_mcp_oauth_endpoints", fake_discover
        )

        await integration_service._refresh_custom_mcp_integration(
            integration=integration,
            refresh_token="refresh-token",
        )

        assert init_calls[-1]["token_endpoint_auth_method"] == "client_secret_basic"

    @pytest.mark.parametrize(
        ("returned_refresh_token", "expected_rotated"),
        [
            ("rotated-refresh-token", True),
            ("refresh-token", False),
            (None, False),
        ],
    )
    async def test_generic_mcp_refresh_detects_rotation_in_log(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        returned_refresh_token: str | None,
        expected_rotated: bool,
    ) -> None:
        """Refresh success log reports rotation by comparing plaintext tokens."""
        provider_key = ProviderKey(
            id=f"custom_mcp_rotation_{uuid.uuid4().hex}",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )
        integration = await integration_service.store_provider_config(
            provider_key=provider_key,
            client_id="rotation-client",
            authorization_endpoint="https://auth.example.test/oauth/authorize",
            token_endpoint="https://auth.example.test/oauth/token",
        )
        session.add(
            MCPIntegration(
                workspace_id=integration_service.workspace_id,
                name="Rotation MCP",
                slug=f"rotation-mcp-{uuid.uuid4().hex}",
                server_type="http",
                server_uri="https://mcp.example.test/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=integration.id,
            )
        )
        await session.commit()

        token_body: dict[str, object] = {
            "access_token": "rotated-access-token",
            "expires_in": 1800,
            "scope": "read",
        }
        if returned_refresh_token is not None:
            token_body["refresh_token"] = returned_refresh_token

        _patch_mcp_oauth_client(monkeypatch, refresh_response=token_body)
        logged = _capture_logger_info(monkeypatch, integration_service.logger)

        await integration_service._refresh_custom_mcp_integration(
            integration=integration,
            refresh_token="refresh-token",
        )

        refresh_logs = [
            kw for msg, kw in logged if msg == "Refreshed MCP OAuth integration"
        ]
        assert len(refresh_logs) == 1
        assert refresh_logs[0]["refresh_token_rotated"] is expected_rotated
        assert refresh_logs[0]["expires_in"] == 1800
        assert refresh_logs[0]["granted_scope"] == "read"

    async def _store_expired_mcp_integration(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
    ) -> OAuthIntegration:
        """Custom MCP OAuth integration whose access token already expired."""
        provider_key = ProviderKey(
            id=f"custom_mcp_dead_refresh_{uuid.uuid4().hex}",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )
        await integration_service.store_provider_config(
            provider_key=provider_key,
            client_id="dead-refresh-client",
            authorization_endpoint="https://auth.example.test/oauth/authorize",
            token_endpoint="https://auth.example.test/oauth/token",
        )
        integration = await integration_service.store_integration(
            provider_key=provider_key,
            access_token=SecretStr("stale-access-token"),
            refresh_token=SecretStr("dead-refresh-token"),
            expires_in=3600,
        )
        integration.expires_at = datetime.now(UTC) - timedelta(hours=1)
        await session.commit()
        await session.refresh(integration)
        return integration

    async def _attach_mcp_to_oauth_integration(
        self,
        *,
        integration_service: IntegrationService,
        session: AsyncSession,
        integration: OAuthIntegration,
    ) -> None:
        session.add(
            MCPIntegration(
                workspace_id=integration_service.workspace_id,
                name="Refresh Response MCP",
                slug=f"refresh-response-mcp-{uuid.uuid4().hex}",
                server_type="http",
                server_uri="https://mcp.example.test/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=integration.id,
            )
        )
        await session.commit()

    async def test_malformed_refresh_response_keeps_refresh_token(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An unusable response does not prove that the old token was spent."""
        integration = await self._store_expired_mcp_integration(
            integration_service, session
        )
        await self._attach_mcp_to_oauth_integration(
            integration_service=integration_service,
            session=session,
            integration=integration,
        )
        _patch_mcp_oauth_client(
            monkeypatch,
            refresh_response={"refresh_token": "rotated-without-access-token"},
        )

        result = await integration_service._refresh_custom_mcp_integration(
            integration=integration,
            refresh_token="dead-refresh-token",
        )

        assert result.encrypted_refresh_token is not None
        assert (
            integration_service._decrypt_token(result.encrypted_refresh_token)
            == "dead-refresh-token"
        )
        assert result.status == IntegrationStatus.CONNECTED

    async def test_refresh_invalid_grant_discards_dead_refresh_token(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Terminal invalid_grant clears the refresh token and flags re-auth."""
        integration = await self._store_expired_mcp_integration(
            integration_service, session
        )

        async def fake_refresh(
            _service: IntegrationService,
            *,
            integration: OAuthIntegration,
            refresh_token: str,
        ) -> OAuthIntegration:
            _ = integration, refresh_token
            raise OAuthError(error="invalid_grant", description="session expired")

        monkeypatch.setattr(
            IntegrationService, "_refresh_custom_mcp_integration", fake_refresh
        )

        result = await integration_service.refresh_token_if_needed(integration)

        assert result.encrypted_refresh_token is None
        assert result.status == IntegrationStatus.REAUTH_REQUIRED

    async def test_refresh_stale_caller_keeps_concurrently_rotated_token(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
    ) -> None:
        """A stale caller observes the token persisted by a concurrent winner."""
        integration = await self._store_expired_mcp_integration(
            integration_service, session
        )
        # Keep the caller's ORM object stale while a winner persists a complete
        # rotation. The refresh transaction must reload this row under its lock
        # and skip presenting the old refresh token.
        session.expunge(integration)
        await session.execute(
            update(OAuthIntegration)
            .where(OAuthIntegration.id == integration.id)
            .values(
                encrypted_access_token=integration_service._encrypt_token(
                    "rotated-access-token"
                ),
                encrypted_refresh_token=integration_service._encrypt_token(
                    "rotated-refresh-token"
                ),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await session.commit()

        result = await integration_service.refresh_token_if_needed(integration)

        assert result.encrypted_refresh_token is not None
        assert (
            integration_service._decrypt_token(result.encrypted_refresh_token)
            == "rotated-refresh-token"
        )
        assert result.status == IntegrationStatus.CONNECTED

    async def test_refresh_transient_error_keeps_refresh_token(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-terminal OAuth errors leave the stored refresh token untouched."""
        integration = await self._store_expired_mcp_integration(
            integration_service, session
        )

        async def fake_refresh(
            _service: IntegrationService,
            *,
            integration: OAuthIntegration,
            refresh_token: str,
        ) -> OAuthIntegration:
            _ = integration, refresh_token
            raise OAuthError(error="temporarily_unavailable", description="down")

        monkeypatch.setattr(
            IntegrationService, "_refresh_custom_mcp_integration", fake_refresh
        )

        result = await integration_service.refresh_token_if_needed(integration)

        assert result.encrypted_refresh_token is not None
        assert result.status == IntegrationStatus.CONNECTED

    async def test_reconnect_catalog_mcp_with_dead_token_returns_oauth_redirect(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A reauth_required row gets a fresh authorize redirect on reconnect.

        Regression: the "already connected" early-return treated a dead token
        as connected, so reconnect skipped OAuth and 401'd at verification.
        """
        catalog = _catalog_entry(
            slug="dead-token-mcp",
            name="Dead Token MCP",
            description="Reconnect regression",
            connection_spec={
                "kind": "http_oauth2",
                "server_type": "http",
                "auth_type": "OAUTH2",
                "requires_config": False,
                "config_fields": [],
                "credentials": [],
                "server_uri": "https://mcp.example.test/mcp",
            },
            sort_key="0000:dead-token-mcp",
        )
        _install_catalog_entry(monkeypatch, catalog)

        integration = await self._store_expired_mcp_integration(
            integration_service, session
        )
        integration.encrypted_refresh_token = b""
        session.add(
            MCPIntegration(
                workspace_id=integration_service.workspace_id,
                name="Dead Token MCP",
                slug="dead-token-mcp",
                catalog_slug=catalog.slug,
                server_type="http",
                server_uri="https://mcp.example.test/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=integration.id,
                tools=[],
            )
        )
        await session.commit()
        await session.refresh(integration)
        assert integration.status == IntegrationStatus.REAUTH_REQUIRED

        await _seed_service_user(session, integration_service)
        _patch_mcp_oauth_client(monkeypatch)

        result = await integration_service.connect_platform_mcp_catalog(
            catalog_slug=catalog.slug
        )

        assert result.oauth_connect is not None
        assert result.oauth_connect.auth_url

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

    async def test_mcp_provider_requests_discovered_offline_access(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Credential-less built-ins request advertised offline access end to end."""

        class DummyMCPProvider(MCPAuthProvider):
            id: str = "offline_mcp"  # type: ignore[assignment]
            mcp_server_uri: str = "https://offline.example/mcp"  # type: ignore[assignment]
            scopes: ProviderScopes = ProviderScopes(default=[])  # type: ignore[assignment]
            metadata: ProviderMetadata = ProviderMetadata(  # type: ignore[assignment]
                id="offline_mcp",
                name="Offline MCP",
                description="MCP provider with advertised offline access",
                requires_config=False,
                enabled=True,
            )

        monkeypatch.setenv("TRACECAT__PUBLIC_APP_URL", "https://app.test")
        discovery = OAuthDiscoveryResult(
            authorization_endpoint="https://offline.example/oauth/authorize",
            token_endpoint="https://offline.example/oauth/token",
            token_methods=["none"],
            scopes_supported=["offline_access"],
            registration_endpoint="https://offline.example/oauth/register",
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

        registration_payload: dict[str, object] = {}

        async def fake_register(
            endpoint: str, payload: dict[str, object]
        ) -> DCRResponse:
            assert endpoint == discovery.registration_endpoint
            registration_payload.update(payload)
            return DCRResponse(
                client_id="offline-client",
                token_endpoint_auth_method="none",
            )

        monkeypatch.setattr(
            DummyMCPProvider,
            "_discover_oauth_endpoints_async",
            classmethod(fake_discover),
        )
        monkeypatch.setattr(
            DummyMCPProvider,
            "_submit_registration_request",
            staticmethod(fake_register),
        )

        provider = await DummyMCPProvider.instantiate()
        auth_url, _ = await provider.get_authorization_url(state="test-state")
        auth_query = parse_qs(urlparse(auth_url).query)

        assert registration_payload["scope"] == "offline_access"
        assert provider.requested_scopes == ["offline_access"]
        assert auth_query["scope"] == ["offline_access"]

    @pytest.mark.parametrize(
        ("scope_echo", "expected_scopes", "expected_authorize_scope"),
        [
            ("read admin", ["read", "admin"], "read admin"),
            ("", [], None),
        ],
    )
    async def test_mcp_provider_honors_dcr_scope_echo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        scope_echo: str,
        expected_scopes: list[str],
        expected_authorize_scope: str | None,
    ) -> None:
        """Credential-less built-ins authorize with the registered scope whitelist."""

        class DummyMCPProvider(MCPAuthProvider):
            id: str = "narrowed_mcp"  # type: ignore[assignment]
            mcp_server_uri: str = "https://narrowed.example/mcp"  # type: ignore[assignment]
            scopes: ProviderScopes = ProviderScopes(default=["read"])  # type: ignore[assignment]
            metadata: ProviderMetadata = ProviderMetadata(  # type: ignore[assignment]
                id="narrowed_mcp",
                name="Narrowed MCP",
                description="MCP provider with echoed registered scopes",
                requires_config=False,
                enabled=True,
            )

        monkeypatch.setenv("TRACECAT__PUBLIC_APP_URL", "https://app.test")
        discovery = OAuthDiscoveryResult(
            authorization_endpoint="https://narrowed.example/oauth/authorize",
            token_endpoint="https://narrowed.example/oauth/token",
            token_methods=["none"],
            scopes_supported=["read", "offline_access"],
            registration_endpoint="https://narrowed.example/oauth/register",
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

        registration_payload: dict[str, object] = {}

        async def fake_register(
            endpoint: str, payload: dict[str, object]
        ) -> DCRResponse:
            assert endpoint == discovery.registration_endpoint
            registration_payload.update(payload)
            return DCRResponse(
                client_id="narrowed-client",
                token_endpoint_auth_method="none",
                scope=scope_echo,
            )

        monkeypatch.setattr(
            DummyMCPProvider,
            "_discover_oauth_endpoints_async",
            classmethod(fake_discover),
        )
        monkeypatch.setattr(
            DummyMCPProvider,
            "_submit_registration_request",
            staticmethod(fake_register),
        )

        provider = await DummyMCPProvider.instantiate()
        auth_url, _ = await provider.get_authorization_url(state="test-state")

        assert registration_payload["scope"] == "read offline_access"
        assert provider.requested_scopes == expected_scopes
        assert parse_qs(urlparse(auth_url).query).get("scope") == (
            [expected_authorize_scope] if expected_authorize_scope is not None else None
        )

    async def test_mcp_provider_sync_constructor_honors_dcr_scope_echo(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The synchronous DCR path applies the same registered-scope narrowing."""

        class DummyMCPProvider(MCPAuthProvider):
            id: str = "sync_narrowed_mcp"  # type: ignore[assignment]
            mcp_server_uri: str = "https://sync-narrowed.example/mcp"  # type: ignore[assignment]
            scopes: ProviderScopes = ProviderScopes(default=["read"])  # type: ignore[assignment]
            metadata: ProviderMetadata = ProviderMetadata(  # type: ignore[assignment]
                id="sync_narrowed_mcp",
                name="Sync Narrowed MCP",
                description="MCP provider with synchronous DCR",
                requires_config=False,
                enabled=True,
            )

        monkeypatch.setenv("TRACECAT__PUBLIC_APP_URL", "https://app.test")

        def fake_register(self: MCPAuthProvider) -> DynamicRegistrationResult:
            assert self._registration_requested_scopes == [
                "read",
                "offline_access",
            ]
            return DynamicRegistrationResult(
                client_id="sync-narrowed-client",
                client_secret=None,
                auth_method="none",
                registered_scopes=["read"],
            )

        monkeypatch.setattr(
            DummyMCPProvider,
            "_perform_dynamic_registration",
            fake_register,
        )

        provider = DummyMCPProvider(
            discovered_auth_endpoint="https://sync-narrowed.example/oauth/authorize",
            discovered_token_endpoint="https://sync-narrowed.example/oauth/token",
            registration_endpoint="https://sync-narrowed.example/oauth/register",
            token_methods=["none"],
            scopes_supported=["read", "offline_access"],
        )
        auth_url, _ = await provider.get_authorization_url(state="test-state")

        assert provider.requested_scopes == ["read"]
        assert parse_qs(urlparse(auth_url).query)["scope"] == ["read"]

    async def test_mcp_provider_instantiate_existing_client_keeps_stored_scopes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reusing a client_id with stored scopes must not re-add offline_access."""

        class DummyMCPProvider(MCPAuthProvider):
            id: str = "existing_client_mcp"  # type: ignore[assignment]
            mcp_server_uri: str = "https://existing.example/mcp"  # type: ignore[assignment]
            scopes: ProviderScopes = ProviderScopes(default=[])  # type: ignore[assignment]
            metadata: ProviderMetadata = ProviderMetadata(  # type: ignore[assignment]
                id="existing_client_mcp",
                name="Existing Client MCP",
                description="MCP provider reusing a stored client",
                requires_config=False,
                enabled=True,
            )

        monkeypatch.setenv("TRACECAT__PUBLIC_APP_URL", "https://app.test")
        discovery = OAuthDiscoveryResult(
            authorization_endpoint="https://existing.example/oauth/authorize",
            token_endpoint="https://existing.example/oauth/token",
            token_methods=["none"],
            scopes_supported=["mcp:read", "offline_access"],
            registration_endpoint="https://existing.example/oauth/register",
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

        async def fail_register(
            endpoint: str, payload: dict[str, object]
        ) -> DCRResponse:
            _ = payload
            raise AssertionError(
                f"DCR must not run with an existing client: {endpoint}"
            )

        monkeypatch.setattr(
            DummyMCPProvider,
            "_discover_oauth_endpoints_async",
            classmethod(fake_discover),
        )
        monkeypatch.setattr(
            DummyMCPProvider,
            "_submit_registration_request",
            staticmethod(fail_register),
        )

        provider_config = ProviderConfig(
            client_id="existing-client",
            scopes=["mcp:read"],
        )
        provider = await DummyMCPProvider.instantiate(config=provider_config)
        auth_url, _ = await provider.get_authorization_url(state="test-state")

        assert provider.requested_scopes == ["mcp:read"]
        assert parse_qs(urlparse(auth_url).query)["scope"] == ["mcp:read"]

    async def test_mcp_provider_sync_existing_client_keeps_stored_scopes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The sync constructor also sends stored scopes verbatim with a known client."""

        class DummyMCPProvider(MCPAuthProvider):
            id: str = "sync_existing_client_mcp"  # type: ignore[assignment]
            mcp_server_uri: str = "https://sync-existing.example/mcp"  # type: ignore[assignment]
            scopes: ProviderScopes = ProviderScopes(default=[])  # type: ignore[assignment]
            metadata: ProviderMetadata = ProviderMetadata(  # type: ignore[assignment]
                id="sync_existing_client_mcp",
                name="Sync Existing Client MCP",
                description="MCP provider reusing a stored client synchronously",
                requires_config=False,
                enabled=True,
            )

        monkeypatch.setenv("TRACECAT__PUBLIC_APP_URL", "https://app.test")

        def fail_register(self: MCPAuthProvider) -> DynamicRegistrationResult:
            raise AssertionError("DCR must not run with an existing client")

        monkeypatch.setattr(
            DummyMCPProvider,
            "_perform_dynamic_registration",
            fail_register,
        )

        provider = DummyMCPProvider(
            client_id="sync-existing-client",
            scopes=["mcp:read"],
            discovered_auth_endpoint="https://sync-existing.example/oauth/authorize",
            discovered_token_endpoint="https://sync-existing.example/oauth/token",
            registration_endpoint="https://sync-existing.example/oauth/register",
            token_methods=["none"],
            scopes_supported=["mcp:read", "offline_access"],
        )
        auth_url, _ = await provider.get_authorization_url(state="test-state")

        assert provider.requested_scopes == ["mcp:read"]
        assert parse_qs(urlparse(auth_url).query)["scope"] == ["mcp:read"]

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
            "scopes_supported": ["offline_access"],
        }
        self._mock_async_discovery(monkeypatch, discovery_doc)

        async def fake_register(
            cls,
            *,
            registration_endpoint: str,
            registration_auth_method: str | None,
            requested_scopes: list[str],
            logger_instance,
        ) -> DynamicRegistrationResult:
            _ = cls, logger_instance
            assert registration_endpoint == "https://www-api.runreveal.com/oauth/client"
            assert registration_auth_method == "client_secret_post"
            assert requested_scopes == ["offline_access"]
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
        auth_url, _ = await provider.get_authorization_url(state="test-state")
        assert parse_qs(urlparse(auth_url).query)["scope"] == ["offline_access"]

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

    @pytest.mark.parametrize(
        ("scope_echo", "expected_registered_scopes"),
        [
            ("read offline_access", ["read", "offline_access"]),
            ("", []),
            (None, None),
        ],
    )
    async def test_mcp_dcr_parses_registered_scopes(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
        scope_echo: str | None,
        expected_registered_scopes: list[str] | None,
    ) -> None:
        """DCR result carries the echoed scope whitelist (None when absent)."""
        response_json: dict[str, object] = {"client_id": "dcr-client"}
        if scope_echo is not None:
            response_json["scope"] = scope_echo
        _patch_mcp_dcr_http(monkeypatch, response_json)

        result = await integration_service._perform_mcp_dynamic_registration(
            registration_endpoint="https://auth.example.test/oauth/register",
            client_name="Test MCP",
            token_auth_method="none",
            requested_scopes=["read", "offline_access"],
        )

        assert result.registered_scopes == expected_registered_scopes

    @pytest.mark.parametrize(
        ("registered_scope_echo", "expected_authorize_scopes"),
        [
            # AS narrowed the registration: authorize with the registered set.
            ("read", ["read"]),
            # An explicit empty echo means no scopes were registered.
            ("", []),
            # AS omitted the scope echo: requested set used verbatim.
            (None, ["read", "write", "offline_access"]),
            # The registration response is authoritative, including added scopes.
            (
                "read write offline_access admin",
                ["read", "write", "offline_access", "admin"],
            ),
        ],
    )
    async def test_connect_mcp_oauth_discovery_uses_effective_scopes(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        registered_scope_echo: str | None,
        expected_authorize_scopes: list[str],
    ) -> None:
        """Authorize with registered scopes, or requested scopes when unreported."""
        await _seed_service_user(session, integration_service)

        endpoints = integration_service_module.MCPOAuthDiscoveryEndpoints(
            authorization_endpoint="https://auth.example.test/oauth/authorize",
            token_endpoint="https://auth.example.test/oauth/token",
            token_methods=["none"],
            registration_endpoint="https://auth.example.test/oauth/register",
            resource="https://mcp.example.test/mcp",
            scopes_supported=["read", "write", "offline_access"],
        )

        async def fake_discover(
            *,
            server_uri: str,
            allowed_endpoint_hosts: frozenset[str] = frozenset(),
        ) -> integration_service_module.MCPOAuthDiscoveryEndpoints:
            _ = server_uri, allowed_endpoint_hosts
            return endpoints

        monkeypatch.setattr(
            integration_service, "_discover_mcp_oauth_endpoints", fake_discover
        )

        async def fake_register(
            *,
            registration_endpoint: str,
            client_name: str,
            token_auth_method: str | None,
            requested_scopes: list[str],
        ) -> integration_service_module.MCPOAuthRegistrationResult:
            _ = registration_endpoint, client_name, token_auth_method, requested_scopes
            return integration_service_module.MCPOAuthRegistrationResult(
                client_id="dcr-client",
                client_secret=None,
                auth_method="none",
                registered_scopes=(
                    registered_scope_echo.split()
                    if registered_scope_echo is not None
                    else None
                ),
            )

        monkeypatch.setattr(
            integration_service, "_perform_mcp_dynamic_registration", fake_register
        )
        _patch_mcp_oauth_client(monkeypatch)

        catalog_spec = MCPHTTPOAuth2ConnectionSpec(
            server_uri="https://mcp.example.test/mcp",
            scopes=["read", "write"],
        )
        result = await integration_service.connect_mcp_oauth_discovery(
            params=MCPHttpIntegrationCreate(
                name="Effective Scopes MCP",
                server_uri="https://mcp.example.test/mcp",
                auth_type=MCPAuthType.OAUTH2,
            ),
            catalog_spec=catalog_spec,
        )

        assert result.oauth_connect is not None
        assert result.mcp_integration is not None
        auth_query = parse_qs(urlparse(result.oauth_connect.auth_url).query)
        assert auth_query.get("scope") == (
            [" ".join(expected_authorize_scopes)] if expected_authorize_scopes else None
        )

        # The effective registered scopes, including an explicit empty set, persist.
        oauth_integration = await integration_service.session.get(
            OAuthIntegration, result.mcp_integration.oauth_integration_id
        )
        assert oauth_integration is not None
        provider_config = integration_service.get_provider_config(
            integration=oauth_integration
        )
        assert provider_config is not None
        assert provider_config.scopes == expected_authorize_scopes

    async def test_connect_then_reconnect_preserves_offline_access(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reconnect over static endpoints keeps the persisted offline_access scope."""
        await _seed_service_user(session, integration_service)

        endpoints = integration_service_module.MCPOAuthDiscoveryEndpoints(
            authorization_endpoint="https://auth.example.test/oauth/authorize",
            token_endpoint="https://auth.example.test/oauth/token",
            token_methods=["none"],
            registration_endpoint="https://auth.example.test/oauth/register",
            resource="https://mcp.example.test/mcp",
            scopes_supported=["read", "offline_access"],
        )

        async def fake_discover(
            *,
            server_uri: str,
            allowed_endpoint_hosts: frozenset[str] = frozenset(),
        ) -> integration_service_module.MCPOAuthDiscoveryEndpoints:
            _ = server_uri, allowed_endpoint_hosts
            return endpoints

        monkeypatch.setattr(
            integration_service, "_discover_mcp_oauth_endpoints", fake_discover
        )

        async def fake_register(
            *,
            registration_endpoint: str,
            client_name: str,
            token_auth_method: str | None,
            requested_scopes: list[str],
        ) -> integration_service_module.MCPOAuthRegistrationResult:
            _ = registration_endpoint, client_name, token_auth_method, requested_scopes
            # No scope echo: effective scopes equal the requested set.
            return integration_service_module.MCPOAuthRegistrationResult(
                client_id="dcr-client",
                client_secret=None,
                auth_method="none",
                registered_scopes=None,
            )

        monkeypatch.setattr(
            integration_service, "_perform_mcp_dynamic_registration", fake_register
        )
        _patch_mcp_oauth_client(monkeypatch)

        catalog_spec = MCPHTTPOAuth2ConnectionSpec(
            server_uri="https://mcp.example.test/mcp",
            scopes=["read"],
        )
        connect_result = await integration_service.connect_mcp_oauth_discovery(
            params=MCPHttpIntegrationCreate(
                name="Round Trip MCP",
                server_uri="https://mcp.example.test/mcp",
                auth_type=MCPAuthType.OAUTH2,
            ),
            catalog_spec=catalog_spec,
        )
        assert connect_result.oauth_connect is not None
        assert connect_result.mcp_integration is not None
        connect_query = parse_qs(urlparse(connect_result.oauth_connect.auth_url).query)
        assert connect_query["scope"] == ["read offline_access"]

        # Persist static endpoints so reconnect resolves scopes_supported=[].
        oauth_integration = await integration_service.session.get(
            OAuthIntegration, connect_result.mcp_integration.oauth_integration_id
        )
        assert oauth_integration is not None
        oauth_integration.authorization_endpoint = endpoints.authorization_endpoint
        oauth_integration.token_endpoint = endpoints.token_endpoint
        integration_service.session.add(oauth_integration)
        await integration_service.session.commit()

        mcp_integration = await integration_service.session.get(
            MCPIntegration, connect_result.mcp_integration.id
        )
        assert mcp_integration is not None

        reconnect_result = await integration_service._start_existing_custom_mcp_oauth(
            mcp_integration=mcp_integration
        )
        assert reconnect_result is not None
        assert reconnect_result.oauth_connect is not None
        reconnect_query = parse_qs(
            urlparse(reconnect_result.oauth_connect.auth_url).query
        )
        assert "offline_access" in reconnect_query["scope"][0].split()

    async def test_reconnect_emits_connect_log(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reconnect path logs provider/scopes so incidents stay traceable."""
        await _seed_service_user(session, integration_service)

        provider_key = ProviderKey(
            id="custom_mcp_reconnect_log",
            grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        )
        oauth_integration = await integration_service.store_provider_config(
            provider_key=provider_key,
            client_id="reconnect-log-client",
            authorization_endpoint="https://auth.example.test/oauth/authorize",
            token_endpoint="https://auth.example.test/oauth/token",
            requested_scopes=["read", "offline_access"],
        )
        mcp_integration = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name="Reconnect Log MCP",
            slug="reconnect-log-mcp",
            server_type="http",
            server_uri="https://mcp.example.test/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=oauth_integration.id,
        )
        session.add(mcp_integration)
        await session.commit()

        _patch_mcp_oauth_client(monkeypatch)
        logged = _capture_logger_info(monkeypatch, integration_service.logger)

        await integration_service._start_existing_custom_mcp_oauth(
            mcp_integration=mcp_integration
        )

        reconnect_logs = [
            kw
            for msg, kw in logged
            if msg == "Reconnecting custom MCP OAuth integration"
        ]
        assert len(reconnect_logs) == 1
        assert reconnect_logs[0]["provider_id"] == provider_key.id
        assert reconnect_logs[0]["scopes_supported"] == []
        logged_requested_scopes = reconnect_logs[0]["requested_scopes"]
        assert isinstance(logged_requested_scopes, list)
        assert "offline_access" in logged_requested_scopes


@pytest.mark.anyio
class TestMCPConnectionVerification:
    """Tests for HTTP MCP config resolution and connection verification."""

    async def test_resolve_http_config_none_auth(
        self, integration_service: IntegrationService
    ) -> None:
        """NONE-auth HTTP integrations resolve with empty headers."""
        mcp_integration = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="No Auth MCP",
                server_uri="https://none.example.com/mcp",
                auth_type=MCPAuthType.NONE,
                timeout=30,
            )
        )

        server_config = await integration_service.resolve_mcp_http_server_config(
            mcp_integration
        )

        assert server_config["url"] == "https://none.example.com/mcp"
        assert server_config.get("headers") == {}
        assert server_config.get("timeout") == 30
        assert server_config.get("id") == str(mcp_integration.id)

    async def test_resolve_http_config_custom_headers(
        self, integration_service: IntegrationService
    ) -> None:
        """CUSTOM-auth integrations resolve their decrypted headers."""
        mcp_integration = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Custom Auth MCP",
                server_uri="https://custom.example.com/mcp",
                auth_type=MCPAuthType.CUSTOM,
                custom_credentials=SecretStr('{"X-API-Key": "secret-key"}'),
            )
        )

        server_config = await integration_service.resolve_mcp_http_server_config(
            mcp_integration
        )

        assert server_config.get("headers") == {"X-API-Key": "secret-key"}

    async def test_resolve_http_config_oauth2_drops_custom_authorization(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """OAuth2 resolution attaches the access token; custom Authorization loses."""
        mcp_integration = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="OAuth MCP",
                server_uri="https://oauth.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
                custom_credentials=SecretStr(
                    '{"authorization": "Bearer attacker", "X-Tenant": "t1"}'
                ),
            )
        )

        server_config = await integration_service.resolve_mcp_http_server_config(
            mcp_integration
        )

        headers = server_config.get("headers")
        assert headers is not None
        assert headers["Authorization"] == "Bearer test_access_token"
        assert "authorization" not in headers
        assert headers["X-Tenant"] == "t1"

    async def test_resolve_http_config_translates_busy_oauth_refresh(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A contended OAuth refresh remains an MCP configuration error."""
        mcp_integration = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Busy OAuth MCP",
                server_uri="https://oauth.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )
        busy_error = OAuthRefreshBusyError("OAuth integration is busy refreshing")
        monkeypatch.setattr(
            IntegrationService,
            "refresh_token_if_needed",
            AsyncMock(side_effect=busy_error),
        )

        with pytest.raises(MCPConfigurationError) as exc_info:
            await integration_service.resolve_mcp_http_server_config(mcp_integration)

        assert exc_info.value.__cause__ is busy_error

    async def test_resolve_mcp_secrets_translates_busy_oauth_refresh(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Secret resolution exposes its documented credential error."""
        mcp_integration = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Busy OAuth Secrets MCP",
                server_uri="https://oauth.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )
        busy_error = OAuthRefreshBusyError("OAuth integration is busy refreshing")
        monkeypatch.setattr(
            IntegrationService,
            "refresh_token_if_needed",
            AsyncMock(side_effect=busy_error),
        )
        preset_service = AgentPresetService(
            session=integration_service.session,
            role=integration_service.role,
        )

        with pytest.raises(MCPSecretResolutionError) as exc_info:
            await preset_service.resolve_mcp_integration_secrets(mcp_integration.id)

        assert isinstance(exc_info.value.__cause__, MCPConfigurationError)
        assert exc_info.value.__cause__.__cause__ is busy_error

    async def test_resolve_http_config_errors(
        self,
        integration_service: IntegrationService,
        session: AsyncSession,
    ) -> None:
        """Unresolvable integrations raise MCPConfigurationError."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Stdio MCP",
                stdio_command="npx",
                stdio_args=["@example/mcp-server"],
            )
        )
        with pytest.raises(MCPConfigurationError):
            await integration_service.resolve_mcp_http_server_config(stdio_integration)

        missing_uri = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name="Missing URI MCP",
            slug="missing-uri-mcp",
            server_type="http",
            server_uri=None,
            auth_type=MCPAuthType.NONE,
        )
        with pytest.raises(MCPConfigurationError):
            await integration_service.resolve_mcp_http_server_config(missing_uri)

        unlinked_oauth = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name="Unlinked OAuth MCP",
            slug="unlinked-oauth-mcp",
            server_type="http",
            server_uri="https://oauth.example.com/mcp",
            auth_type=MCPAuthType.OAUTH2,
            oauth_integration_id=None,
        )
        with pytest.raises(MCPConfigurationError):
            await integration_service.resolve_mcp_http_server_config(unlinked_oauth)

        custom_without_headers = MCPIntegration(
            workspace_id=integration_service.workspace_id,
            name="Custom No Headers MCP",
            slug="custom-no-headers-mcp",
            server_type="http",
            server_uri="https://custom.example.com/mcp",
            auth_type=MCPAuthType.CUSTOM,
            encrypted_headers=None,
        )
        with pytest.raises(MCPConfigurationError):
            await integration_service.resolve_mcp_http_server_config(
                custom_without_headers
            )

    async def test_verify_stdio_persists_discovered_tools(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Saved stdio verification refreshes and stores discovered tools."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Stdio Verify MCP",
                stdio_command="npx",
                stdio_args=["@example/mcp-server"],
            )
        )
        stdio_integration.tools = [
            MCPToolSummary(
                name="old_tool",
                description="Old",
                enabled=False,
                requires_approval=True,
            ).model_dump()
        ]
        integration_service.session.add(stdio_integration)
        await integration_service.session.commit()

        async def _probe_stdio(
            _mcp_integration: MCPIntegration,
        ) -> list[MCPToolSummary]:
            return [MCPToolSummary(name="new_tool", description="New")]

        monkeypatch.setattr(
            integration_service,
            "_probe_mcp_stdio_server",
            _probe_stdio,
        )

        result = await integration_service.verify_mcp_integration(
            mcp_integration=stdio_integration
        )

        assert result.success is True
        assert result.message == "Connected successfully — 1 tools available"
        assert result.tools is not None
        assert [tool.name for tool in result.tools] == ["new_tool", "old_tool"]
        assert result.tools[1].status == "missing"

    async def test_connect_gate_starts_stdio_verification_in_background(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Connect-time stdio verification is scheduled without awaiting a probe."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Async Stdio Connect MCP",
                stdio_command="npx",
                stdio_args=["@example/mcp-server"],
            )
        )
        scheduled: list[uuid.UUID] = []

        async def _verify_should_not_run(**_: object) -> object:
            raise AssertionError("stdio connect should not await verification")

        async def _start_stdio_verification(*, mcp_integration: MCPIntegration) -> None:
            scheduled.append(mcp_integration.id)

        monkeypatch.setattr(
            integration_service,
            "verify_mcp_integration",
            _verify_should_not_run,
        )
        monkeypatch.setattr(
            integration_service,
            "start_mcp_stdio_verification",
            _start_stdio_verification,
        )

        await integration_router_module._gate_mcp_connect_verification(
            integration_service,
            stdio_integration,
        )

        assert scheduled == [stdio_integration.id]
        assert (
            await integration_service.mcp_integration_state(
                mcp_integration=stdio_integration
            )
            == "configured"
        )

    async def test_start_stdio_verification_starts_durable_workflow(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Detached stdio verification starts a durable probe-and-persist workflow."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Background Stdio Verify MCP",
                stdio_command="npx",
                stdio_args=["@example/mcp-server"],
            )
        )
        start_workflow = AsyncMock()
        monkeypatch.setattr(
            integration_service_module,
            "get_temporal_client",
            AsyncMock(return_value=SimpleNamespace(start_workflow=start_workflow)),
        )

        await integration_service.start_mcp_stdio_verification(
            mcp_integration=stdio_integration
        )

        start_workflow.assert_awaited_once()
        call = start_workflow.await_args
        assert call is not None
        workflow_input = call.args[1]
        assert isinstance(workflow_input, StdioMCPProbeWorkflowInput)
        assert workflow_input.mcp_integration_id == stdio_integration.id
        assert workflow_input.role == integration_service.role
        assert workflow_input.persist_result is True
        assert call.kwargs["id"] == build_stdio_mcp_probe_workflow_id(
            workspace_id=integration_service.workspace_id,
            mcp_integration_id=stdio_integration.id,
        )
        assert call.kwargs["id_reuse_policy"] == WorkflowIDReusePolicy.ALLOW_DUPLICATE
        assert (
            call.kwargs["id_conflict_policy"]
            == WorkflowIDConflictPolicy.TERMINATE_EXISTING
        )

    async def test_start_stdio_verification_supersedes_in_flight_workflow(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A later save starts a replacement workflow for the deterministic id."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Background Stdio Supersede MCP",
                stdio_command="npx",
                stdio_args=["@example/mcp-server"],
            )
        )
        start_workflow = AsyncMock()
        monkeypatch.setattr(
            integration_service_module,
            "get_temporal_client",
            AsyncMock(return_value=SimpleNamespace(start_workflow=start_workflow)),
        )

        await integration_service.start_mcp_stdio_verification(
            mcp_integration=stdio_integration
        )
        await integration_service.start_mcp_stdio_verification(
            mcp_integration=stdio_integration
        )

        assert start_workflow.await_count == 2
        expected_id = build_stdio_mcp_probe_workflow_id(
            workspace_id=integration_service.workspace_id,
            mcp_integration_id=stdio_integration.id,
        )
        for call in start_workflow.await_args_list:
            assert call.kwargs["id"] == expected_id
            assert (
                call.kwargs["id_conflict_policy"]
                == WorkflowIDConflictPolicy.TERMINATE_EXISTING
            )

    async def test_stdio_verification_status_running_is_verifying(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A running stdio probe workflow is reported as verifying."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Status Running Stdio MCP",
                stdio_command="npx",
                stdio_args=["@example/mcp-server"],
            )
        )
        handle = SimpleNamespace(
            describe=AsyncMock(
                return_value=SimpleNamespace(status=WorkflowExecutionStatus.RUNNING)
            ),
            result=AsyncMock(),
        )
        get_workflow_handle = Mock(return_value=handle)
        monkeypatch.setattr(
            integration_service_module,
            "get_temporal_client",
            AsyncMock(
                return_value=SimpleNamespace(get_workflow_handle=get_workflow_handle)
            ),
        )

        status_read = await integration_service.get_stdio_mcp_verification_status(
            mcp_integration=stdio_integration
        )

        assert status_read.status == "verifying"
        assert status_read.error is None
        get_workflow_handle.assert_called_once_with(
            build_stdio_mcp_probe_workflow_id(
                workspace_id=integration_service.workspace_id,
                mcp_integration_id=stdio_integration.id,
            ),
            result_type=StdioMCPProbeResult,
        )
        handle.result.assert_not_awaited()

    async def test_stdio_verification_status_completed_success_is_succeeded(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A successful completed stdio probe workflow is reported as succeeded."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Status Success Stdio MCP",
                stdio_command="npx",
                stdio_args=["@example/mcp-server"],
            )
        )
        handle = SimpleNamespace(
            describe=AsyncMock(
                return_value=SimpleNamespace(status=WorkflowExecutionStatus.COMPLETED)
            ),
            result=AsyncMock(
                return_value=StdioMCPProbeResult(
                    success=True,
                    tools=[],
                    message="Connected successfully",
                )
            ),
        )
        monkeypatch.setattr(
            integration_service_module,
            "get_temporal_client",
            AsyncMock(
                return_value=SimpleNamespace(
                    get_workflow_handle=Mock(return_value=handle)
                )
            ),
        )

        status_read = await integration_service.get_stdio_mcp_verification_status(
            mcp_integration=stdio_integration
        )

        assert status_read.status == "succeeded"
        assert status_read.error is None
        handle.result.assert_awaited_once()

    async def test_stdio_verification_status_completed_failure_is_failed(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A completed failed stdio probe returns its sanitized error."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Status Failure Stdio MCP",
                stdio_command="npx",
                stdio_args=["@example/mcp-server"],
            )
        )
        handle = SimpleNamespace(
            describe=AsyncMock(
                return_value=SimpleNamespace(status=WorkflowExecutionStatus.COMPLETED)
            ),
            result=AsyncMock(
                return_value=StdioMCPProbeResult(
                    success=False,
                    tools=[],
                    message="Failed to connect to the stdio MCP server",
                    error=("Failed https://user:secret@example.com/path?token=abc"),
                )
            ),
        )
        monkeypatch.setattr(
            integration_service_module,
            "get_temporal_client",
            AsyncMock(
                return_value=SimpleNamespace(
                    get_workflow_handle=Mock(return_value=handle)
                )
            ),
        )

        status_read = await integration_service.get_stdio_mcp_verification_status(
            mcp_integration=stdio_integration
        )

        assert status_read.status == "failed"
        assert status_read.error == "Failed https://example.com/path"
        assert "secret" not in status_read.error
        assert "token" not in status_read.error
        handle.result.assert_awaited_once()

    async def test_stdio_verification_status_not_found_is_idle(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A missing stdio probe workflow is reported as idle."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Status Missing Stdio MCP",
                stdio_command="npx",
                stdio_args=["@example/mcp-server"],
            )
        )
        handle = SimpleNamespace(
            describe=AsyncMock(
                side_effect=RPCError("not found", RPCStatusCode.NOT_FOUND, b"")
            ),
            result=AsyncMock(),
        )
        monkeypatch.setattr(
            integration_service_module,
            "get_temporal_client",
            AsyncMock(
                return_value=SimpleNamespace(
                    get_workflow_handle=Mock(return_value=handle)
                )
            ),
        )

        status_read = await integration_service.get_stdio_mcp_verification_status(
            mcp_integration=stdio_integration
        )

        assert status_read.status == "idle"
        assert status_read.error is None
        handle.result.assert_not_awaited()

    async def test_stdio_verification_status_terminated_is_superseded(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A terminated stdio probe workflow is reported as superseded."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Status Terminated Stdio MCP",
                stdio_command="npx",
                stdio_args=["@example/mcp-server"],
            )
        )
        handle = SimpleNamespace(
            describe=AsyncMock(
                return_value=SimpleNamespace(status=WorkflowExecutionStatus.TERMINATED)
            ),
            result=AsyncMock(),
        )
        monkeypatch.setattr(
            integration_service_module,
            "get_temporal_client",
            AsyncMock(
                return_value=SimpleNamespace(
                    get_workflow_handle=Mock(return_value=handle)
                )
            ),
        )

        status_read = await integration_service.get_stdio_mcp_verification_status(
            mcp_integration=stdio_integration
        )

        assert status_read.status == "superseded"
        assert status_read.error is None
        handle.result.assert_not_awaited()

    async def test_blocking_stdio_probe_supersedes_in_flight_workflow(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Blocking stdio probes use the same deterministic latest-wins id."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Blocking Stdio Supersede MCP",
                stdio_command="npx",
                stdio_args=["@example/mcp-server"],
            )
        )
        execute_workflow = AsyncMock(
            return_value=StdioMCPProbeResult(
                success=True,
                tools=[MCPToolSummary(name="fresh_tool", description="Fresh")],
                message="Connected successfully",
            )
        )
        monkeypatch.setattr(
            integration_service_module,
            "get_temporal_client",
            AsyncMock(return_value=SimpleNamespace(execute_workflow=execute_workflow)),
        )

        tools = await integration_service._probe_mcp_stdio_server(stdio_integration)

        assert [tool.name for tool in tools] == ["fresh_tool"]
        execute_workflow.assert_awaited_once()
        call = execute_workflow.await_args
        assert call is not None
        workflow_input = call.args[1]
        assert isinstance(workflow_input, StdioMCPProbeWorkflowInput)
        assert workflow_input.mcp_integration_id == stdio_integration.id
        assert workflow_input.role == integration_service.role
        assert workflow_input.persist_result is False
        assert call.kwargs["id"] == build_stdio_mcp_probe_workflow_id(
            workspace_id=integration_service.workspace_id,
            mcp_integration_id=stdio_integration.id,
        )
        assert call.kwargs["id_reuse_policy"] == WorkflowIDReusePolicy.ALLOW_DUPLICATE
        assert (
            call.kwargs["id_conflict_policy"]
            == WorkflowIDConflictPolicy.TERMINATE_EXISTING
        )

    async def test_blocking_stdio_probe_reports_superseded_workflow(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A terminated blocking probe reports that a newer probe superseded it."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Blocking Stdio Terminated MCP",
                stdio_command="npx",
                stdio_args=["@example/mcp-server"],
            )
        )
        execute_workflow = AsyncMock(
            side_effect=WorkflowFailureError(cause=TerminatedError("terminated"))
        )
        monkeypatch.setattr(
            integration_service_module,
            "get_temporal_client",
            AsyncMock(return_value=SimpleNamespace(execute_workflow=execute_workflow)),
        )

        with pytest.raises(MCPConnectionVerificationError) as exc_info:
            await integration_service._probe_mcp_stdio_server(stdio_integration)

        assert (
            exc_info.value.message
            == "Stdio MCP verification was superseded by a newer verification"
        )
        assert exc_info.value.error == "Superseded by a newer verification"

    def test_stdio_test_connection_rejects_inline_config_without_saved_id(
        self,
    ) -> None:
        """Stdio test-connection requires a saved integration row by ID."""
        adapter: TypeAdapter[MCPIntegrationTestConnectionRequest] = TypeAdapter(
            MCPIntegrationTestConnectionRequest
        )

        with pytest.raises(ValidationError) as exc_info:
            adapter.validate_python(
                {
                    "server_type": "stdio",
                    "stdio_command": "npx",
                    "stdio_args": ["@example/server"],
                    "stdio_env": {"TOKEN": "inline-token"},
                }
            )

        error_types = {error["type"] for error in exc_info.value.errors()}
        assert "missing" in error_types
        assert "extra_forbidden" in error_types

    async def test_saved_stdio_test_connection_persists_discovered_tools(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Stdio tests use saved-row verification and persist discovered tools."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Stdio Saved Test MCP",
                stdio_command="npx",
                stdio_args=["@example/server"],
                stdio_env={"TOKEN": "stored-token"},
            )
        )

        def _decrypt_stdio_env_should_not_run(*_: object, **__: object) -> None:
            raise AssertionError("stored env must be loaded inside the activity")

        async def _probe_stdio(
            mcp_integration: MCPIntegration,
        ) -> list[MCPToolSummary]:
            assert mcp_integration.id == stdio_integration.id
            assert mcp_integration.stdio_args == ["@example/server"]
            return [MCPToolSummary(name="saved_tool", description="Saved")]

        monkeypatch.setattr(
            integration_service,
            "decrypt_stdio_env",
            _decrypt_stdio_env_should_not_run,
        )
        monkeypatch.setattr(
            integration_service,
            "_probe_mcp_stdio_server",
            _probe_stdio,
        )

        result = await integration_service.test_mcp_connection(
            params=MCPStdioIntegrationTestConnectionRequest(
                mcp_integration_id=stdio_integration.id,
                server_type="stdio",
            )
        )
        await integration_service.session.refresh(stdio_integration)

        assert result.success is True
        assert result.message == "Connected successfully — 1 tools available"
        assert result.tools is not None
        assert [tool.name for tool in result.tools] == ["saved_tool"]
        stored_tools = MCPToolSummary.validate_stored(
            stdio_integration.tools, mcp_integration_id=stdio_integration.id
        )
        assert stored_tools is not None
        assert [tool.name for tool in stored_tools] == ["saved_tool"]

    async def test_update_stdio_metadata_with_null_env_skips_verification(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A serialized null env does not dirty otherwise unchanged stdio config."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Stdio Metadata MCP",
                description="Original",
                stdio_command="npx",
                stdio_env={"TOKEN": "stored-token"},
            )
        )
        stored_env = stdio_integration.encrypted_stdio_env
        assert stored_env is not None
        stdio_integration.timeout = None
        stdio_integration.tools = [
            MCPToolSummary(
                name="existing_tool",
                description="Existing",
                enabled=False,
            ).model_dump()
        ]
        integration_service.session.add(stdio_integration)
        await integration_service.session.commit()

        verify_mcp_integration = AsyncMock(
            side_effect=AssertionError("metadata-only edit must not verify")
        )
        monkeypatch.setattr(
            integration_service,
            "verify_mcp_integration",
            verify_mcp_integration,
        )

        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=stdio_integration.id,
            params=MCPIntegrationUpdate.model_validate(
                {
                    "name": "Renamed Stdio Metadata MCP",
                    "description": "Updated",
                    "server_type": "stdio",
                    "stdio_command": "npx",
                    "stdio_args": [],
                    "stdio_env": None,
                    "timeout": 30,
                }
            ),
            verify_connection=True,
        )

        assert updated is not None
        verify_mcp_integration.assert_not_awaited()
        assert updated.name == "Renamed Stdio Metadata MCP"
        assert updated.description == "Updated"
        assert updated.stdio_args == []
        assert updated.encrypted_stdio_env == stored_env
        assert updated.timeout == 30
        tools = MCPToolSummary.validate_stored(updated.tools)
        assert tools is not None
        assert [tool.name for tool in tools] == ["existing_tool"]
        assert tools[0].enabled is False

    @pytest.mark.parametrize(
        "update_params",
        [
            MCPIntegrationUpdate(
                server_type="stdio",
                stdio_command="uvx",
                stdio_args=["@example/server"],
                timeout=30,
            ),
            MCPIntegrationUpdate(
                server_type="stdio",
                stdio_command="npx",
                stdio_args=["@example/other-server"],
                timeout=30,
            ),
            MCPIntegrationUpdate(
                server_type="stdio",
                stdio_command="npx",
                stdio_args=["@example/server"],
                timeout=45,
            ),
            MCPIntegrationUpdate(
                server_type="stdio",
                stdio_command="npx",
                stdio_args=["@example/server"],
                stdio_env={"TOKEN": "changed"},
                timeout=30,
            ),
        ],
        ids=["command", "args", "timeout", "env"],
    )
    async def test_update_stdio_connection_change_runs_verification(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
        update_params: MCPIntegrationUpdate,
    ) -> None:
        """Actual command, args, timeout, or env edits run saved-row verification."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Stdio Dirty Update MCP",
                stdio_command="npx",
                stdio_args=["@example/server"],
                timeout=30,
            )
        )
        stdio_integration.tools = [
            MCPToolSummary(name="existing_tool", description="Existing").model_dump()
        ]
        integration_service.session.add(stdio_integration)
        await integration_service.session.commit()
        verified_ids: list[uuid.UUID] = []

        async def _verify(
            *,
            mcp_integration: MCPIntegration,
            previous_tools: list[dict[str, object]] | None = None,
        ) -> MCPIntegrationTestConnectionResponse:
            assert previous_tools is not None
            assert mcp_integration.tools is None
            verified_ids.append(mcp_integration.id)
            return MCPIntegrationTestConnectionResponse(
                success=True,
                mcp_integration_id=mcp_integration.id,
                tools=[MCPToolSummary(name="verified_tool", description="Verified")],
                message="Connected successfully",
            )

        monkeypatch.setattr(
            integration_service,
            "verify_mcp_integration",
            _verify,
        )

        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=stdio_integration.id,
            params=update_params,
            verify_connection=True,
        )

        assert updated is not None
        assert verified_ids == [stdio_integration.id]

    async def test_update_stdio_with_verification_persists_discovered_tools(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Saving a dirty stdio config verifies it and stores discovered tools."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Stdio Update Verify MCP",
                stdio_command="npx",
                stdio_args=["@example/old-server"],
            )
        )
        stdio_integration.tools = [
            MCPToolSummary(
                name="old_tool",
                description="Old",
                enabled=False,
                requires_approval=True,
            ).model_dump()
        ]
        integration_service.session.add(stdio_integration)
        await integration_service.session.commit()

        async def _probe_stdio(
            mcp_integration: MCPIntegration,
        ) -> list[MCPToolSummary]:
            assert mcp_integration.id == stdio_integration.id
            assert mcp_integration.stdio_command == "npx"
            assert mcp_integration.stdio_args == ["@example/new-server"]
            assert mcp_integration.tools is None
            return [MCPToolSummary(name="new_tool", description="New")]

        monkeypatch.setattr(
            integration_service,
            "_probe_mcp_stdio_server",
            _probe_stdio,
        )

        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=stdio_integration.id,
            params=MCPIntegrationUpdate(
                server_type="stdio",
                stdio_command="npx",
                stdio_args=["@example/new-server"],
            ),
            verify_connection=True,
        )

        assert updated is not None
        tools = MCPToolSummary.validate_stored(updated.tools)
        assert tools is not None
        assert [tool.name for tool in tools] == ["new_tool", "old_tool"]
        assert tools[1].enabled is False
        assert tools[1].requires_approval is True
        assert tools[1].status == "missing"

    async def test_update_stdio_verification_failure_raises_after_saving_config(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Failed stdio update verification raises and leaves the saved config."""
        stdio_integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Stdio Failed Update Verify MCP",
                stdio_command="npx",
                stdio_args=["@example/old-server"],
            )
        )
        stdio_integration.tools = [
            MCPToolSummary(name="old_tool", description="Old").model_dump()
        ]
        integration_service.session.add(stdio_integration)
        await integration_service.session.commit()

        async def _probe_stdio(
            mcp_integration: MCPIntegration,
        ) -> list[MCPToolSummary]:
            assert mcp_integration.id == stdio_integration.id
            assert mcp_integration.stdio_args == ["@example/bad-server"]
            assert mcp_integration.tools is None
            raise MCPConnectionVerificationError("Connection failed", "bad config")

        monkeypatch.setattr(
            integration_service,
            "_probe_mcp_stdio_server",
            _probe_stdio,
        )

        with pytest.raises(MCPConnectionVerificationError) as exc_info:
            await integration_service.update_mcp_integration(
                mcp_integration_id=stdio_integration.id,
                params=MCPIntegrationUpdate(
                    server_type="stdio",
                    stdio_command="npx",
                    stdio_args=["@example/bad-server"],
                ),
                verify_connection=True,
            )

        assert exc_info.value.message == "Connection failed"
        assert exc_info.value.error == "bad config"
        await integration_service.session.refresh(stdio_integration)
        assert stdio_integration.stdio_args == ["@example/bad-server"]
        assert stdio_integration.tools is None
        assert (
            await integration_service.mcp_integration_state(
                mcp_integration=stdio_integration
            )
            == "configured"
        )

    def test_merge_mcp_tool_summaries_preserves_policy_and_marks_missing(self) -> None:
        stored = [
            {
                "name": "search",
                "description": "Old search",
                "enabled": False,
                "requires_approval": True,
                "status": "available",
            },
            {
                "name": "delete",
                "description": "Delete issue",
                "enabled": True,
                "requires_approval": True,
                "status": "available",
            },
        ]

        merged = IntegrationService._merge_mcp_tool_summaries(
            [
                MCPToolSummary(name="search", description="New search"),
                MCPToolSummary(name="create", description="Create issue"),
            ],
            stored,
        )

        assert [tool.name for tool in merged] == ["search", "create", "delete"]
        assert merged[0].description == "New search"
        assert merged[0].enabled is False
        assert merged[0].requires_approval is True
        assert merged[0].status == "available"
        assert merged[1].enabled is True
        assert merged[1].requires_approval is False
        assert merged[1].status == "available"
        assert merged[2].status == "missing"
        assert merged[2].enabled is True
        assert merged[2].requires_approval is True

    async def test_persist_mcp_integration_tools_merges_policy(
        self,
        integration_service: IntegrationService,
    ) -> None:
        integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Persist Merge MCP",
                stdio_command="npx",
                stdio_args=["@example/server"],
            )
        )
        integration.tools = [
            MCPToolSummary(
                name="search",
                description="Old search",
                enabled=False,
                requires_approval=True,
            ).model_dump(),
            MCPToolSummary(
                name="delete",
                description="Delete issue",
                enabled=True,
                requires_approval=True,
            ).model_dump(),
        ]
        integration_service.session.add(integration)
        await integration_service.session.commit()

        merged = await integration_service.persist_mcp_integration_tools(
            mcp_integration_id=integration.id,
            discovered_tools=[
                MCPToolSummary(name="search", description="New search"),
                MCPToolSummary(name="create", description="Create issue"),
            ],
        )
        await integration_service.session.refresh(integration)

        assert [tool.name for tool in merged] == ["search", "create", "delete"]
        assert merged[0].enabled is False
        assert merged[0].requires_approval is True
        assert merged[1].enabled is True
        assert merged[1].requires_approval is False
        assert merged[2].status == "missing"
        stored_tools = MCPToolSummary.validate_stored(integration.tools)
        assert stored_tools is not None
        assert [tool.name for tool in stored_tools] == ["search", "create", "delete"]

    async def test_update_mcp_tool_policies(
        self, integration_service: IntegrationService
    ) -> None:
        integration = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Tool Policy MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.NONE,
            )
        )
        integration.tools = [
            MCPToolSummary(name="search", description="Search").model_dump(),
            MCPToolSummary(name="delete", description="Delete").model_dump(),
        ]
        integration_service.session.add(integration)
        await integration_service.session.commit()

        updated = await integration_service.update_mcp_tool_policies(
            mcp_integration_id=integration.id,
            tools=[
                MCPToolPolicyUpdate(
                    name="delete",
                    enabled=False,
                    requires_approval=True,
                )
            ],
        )

        assert updated is not None
        tools = MCPToolSummary.validate_stored(updated.tools)
        assert tools is not None
        by_name = {tool.name: tool for tool in tools}
        assert by_name["search"].enabled is True
        assert by_name["search"].requires_approval is False
        assert by_name["delete"].enabled is False
        assert by_name["delete"].requires_approval is True

    async def test_update_mcp_tool_policies_rejects_approval_without_entitlement(
        self,
        integration_service: IntegrationService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Enabling approval needs AGENT_ADDONS; disabling/availability do not."""
        monkeypatch.setattr(
            tier_defaults,
            "DEFAULT_ENTITLEMENTS",
            tier_defaults.DEFAULT_ENTITLEMENTS.model_copy(
                update={"agent_addons": False}
            ),
        )
        integration = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Unentitled Tool Policy MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.NONE,
            )
        )
        integration.tools = [
            MCPToolSummary(name="search", description="Search").model_dump(),
            MCPToolSummary(name="delete", description="Delete").model_dump(),
        ]
        integration_service.session.add(integration)
        await integration_service.session.commit()

        with pytest.raises(EntitlementRequired, match="agent_addons"):
            await integration_service.update_mcp_tool_policies(
                mcp_integration_id=integration.id,
                tools=[MCPToolPolicyUpdate(name="delete", requires_approval=True)],
            )

        # Disabling availability and turning approval back off stay allowed.
        updated = await integration_service.update_mcp_tool_policies(
            mcp_integration_id=integration.id,
            tools=[
                MCPToolPolicyUpdate(name="search", enabled=False),
                MCPToolPolicyUpdate(name="delete", requires_approval=False),
            ],
        )
        assert updated is not None
        tools = MCPToolSummary.validate_stored(updated.tools)
        assert tools is not None
        by_name = {tool.name: tool for tool in tools}
        assert by_name["search"].enabled is False
        assert by_name["delete"].requires_approval is False

    async def test_update_mcp_tool_policies_rejects_approval_for_stdio(
        self, integration_service: IntegrationService
    ) -> None:
        """Enabling approval on a stdio MCP tool is not supported and rejected.

        The stdio subprocess lives inside the per-turn sandbox and is gone by
        the time the approval continuation runs, so an approved call could
        never execute. Enabling approval must be rejected before it is stored.
        """
        integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Stdio Tool Policy MCP",
                stdio_command="uvx",
                stdio_args=["example-mcp"],
            )
        )
        integration.tools = [
            MCPToolSummary(name="search", description="Search").model_dump(),
            MCPToolSummary(name="delete", description="Delete").model_dump(),
        ]
        integration_service.session.add(integration)
        await integration_service.session.commit()

        with pytest.raises(
            ValueError, match="Approvals are not supported for stdio MCP servers"
        ):
            await integration_service.update_mcp_tool_policies(
                mcp_integration_id=integration.id,
                tools=[MCPToolPolicyUpdate(name="delete", requires_approval=True)],
            )

    async def test_update_mcp_tool_policies_allows_disable_approval_for_stdio(
        self, integration_service: IntegrationService
    ) -> None:
        """Disabling approval and availability changes stay allowed for stdio."""
        integration = await integration_service.create_mcp_integration(
            params=MCPStdioIntegrationCreate(
                name="Stdio Tool Policy MCP Allow",
                stdio_command="uvx",
                stdio_args=["example-mcp"],
            )
        )
        integration.tools = [
            MCPToolSummary(
                name="delete", description="Delete", requires_approval=True
            ).model_dump(),
        ]
        integration_service.session.add(integration)
        await integration_service.session.commit()

        updated = await integration_service.update_mcp_tool_policies(
            mcp_integration_id=integration.id,
            tools=[
                MCPToolPolicyUpdate(
                    name="delete", enabled=False, requires_approval=False
                )
            ],
        )
        assert updated is not None
        tools = MCPToolSummary.validate_stored(updated.tools)
        assert tools is not None
        by_name = {tool.name: tool for tool in tools}
        assert by_name["delete"].enabled is False
        assert by_name["delete"].requires_approval is False


class TestMCPTestConnectionRequestSchema:
    """Input validation for HTTP MCP draft-test server_uri."""

    @pytest.mark.parametrize(
        "server_uri",
        [
            "ftp://api.example.com/mcp",
            "file:///etc/passwd",
            "api.example.com/mcp",  # no scheme
            "https:///mcp",  # no host
        ],
    )
    def test_rejects_invalid_server_uri(self, server_uri: str) -> None:
        with pytest.raises(ValueError):
            MCPHttpIntegrationTestConnectionRequest(server_uri=server_uri)

    def test_strips_and_accepts_valid_uri(self) -> None:
        request = MCPHttpIntegrationTestConnectionRequest(
            server_uri="  https://api.example.com/mcp  "
        )
        assert request.server_uri == "https://api.example.com/mcp"

    @pytest.mark.parametrize(
        "server_uri",
        [
            "http://localhost:8080/mcp",
            "http://127.0.0.1/mcp",
            "https://api.example.com/mcp",
        ],
    )
    def test_accepts_localhost_for_self_hosted(self, server_uri: str) -> None:
        """Loopback hosts are valid so self-hosted MCP servers can connect."""
        request = MCPHttpIntegrationTestConnectionRequest(server_uri=server_uri)
        assert request.server_uri == server_uri


class TestCatalogToolsGuard:
    """A single malformed stored tool must not crash catalog listing."""

    def test_skips_malformed_tool_entries(self) -> None:
        integration = MCPIntegration(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            name="Catalog MCP",
            slug="catalog-mcp",
            server_type="http",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.NONE,
            tools=[
                {"name": "good", "description": "ok"},
                {"description": "missing required name"},  # invalid
                {"name": "also_good", "description": None},
            ],
        )

        tools = PlatformMCPCatalogService._catalog_tools(integration)

        assert tools is not None
        assert [t.name for t in tools] == ["good", "also_good"]

    def test_returns_none_when_unverified(self) -> None:
        integration = MCPIntegration(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            name="Catalog MCP",
            slug="catalog-mcp",
            server_type="http",
            server_uri="https://api.example.com/mcp",
            auth_type=MCPAuthType.NONE,
            tools=None,
        )

        assert PlatformMCPCatalogService._catalog_tools(integration) is None
        assert PlatformMCPCatalogService._catalog_tools(None) is None


@pytest.mark.anyio
class TestMCPOAuthAuthorizationPending:
    """The connect/save verification gate must skip pre-authorization OAuth."""

    async def test_pending_when_oauth_token_absent(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """A linked-but-unauthorized OAuth integration reads as pending."""
        oauth_integration.encrypted_access_token = b""
        integration_service.session.add(oauth_integration)
        await integration_service.session.commit()

        mcp_integration = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Pending OAuth MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )

        assert (
            await integration_service.mcp_oauth_authorization_pending(
                mcp_integration=mcp_integration
            )
            is True
        )

    async def test_not_pending_when_oauth_token_present(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
    ) -> None:
        """An authorized OAuth integration (token stored) is not pending."""
        mcp_integration = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Authorized OAuth MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )

        assert (
            await integration_service.mcp_oauth_authorization_pending(
                mcp_integration=mcp_integration
            )
            is False
        )

    async def test_non_oauth_never_pending(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """NONE/CUSTOM auth integrations are never gated on a token."""
        mcp_integration = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="No Auth MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.NONE,
            )
        )

        assert (
            await integration_service.mcp_oauth_authorization_pending(
                mcp_integration=mcp_integration
            )
            is False
        )

    async def test_update_skips_verification_when_oauth_pending(
        self,
        integration_service: IntegrationService,
        oauth_integration: OAuthIntegration,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A benign edit of an unauthorized OAuth MCP server must not be probed.

        The connect/save gate skips verification while OAuth is pending; the
        update path must do the same, otherwise a rename/timeout bump on an
        integration awaiting authorization is rejected with a 502.
        """
        oauth_integration.encrypted_access_token = b""
        integration_service.session.add(oauth_integration)
        await integration_service.session.commit()

        mcp_integration = await integration_service.create_mcp_integration(
            params=MCPHttpIntegrationCreate(
                name="Pending OAuth MCP",
                server_uri="https://api.example.com/mcp",
                auth_type=MCPAuthType.OAUTH2,
                oauth_integration_id=oauth_integration.id,
            )
        )

        async def _fail_probe(*args: object, **kwargs: object) -> object:
            raise AssertionError("probe must be skipped while OAuth is pending")

        monkeypatch.setattr(integration_service, "_probe_mcp_http_server", _fail_probe)

        updated = await integration_service.update_mcp_integration(
            mcp_integration_id=mcp_integration.id,
            params=MCPIntegrationUpdate(name="Pending OAuth MCP renamed"),
            verify_connection=True,
        )

        assert updated is not None
        assert updated.name == "Pending OAuth MCP renamed"
