"""Test suite for MCP integrations.

This test suite covers MCP integration functionality including:
- CRUD operations for all auth types (OAuth2, Custom, None)
- Authentication type switching and credential swapping
- Workspace isolation
- Validation and edge cases
- MCP provider OAuth discovery behavior
"""

import uuid

import pytest
from pydantic import SecretStr, TypeAdapter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES
from tracecat.db.models import AgentPreset, MCPIntegration, OAuthIntegration
from tracecat.integrations.enums import MCPAuthType, OAuthGrantType
from tracecat.integrations.providers.base import (
    MCPAuthProvider,
    OAuthDiscoveryResult,
)
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
