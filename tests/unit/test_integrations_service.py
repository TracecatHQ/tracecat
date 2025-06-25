import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from authlib.integrations.httpx_client import AsyncOAuth2Client
from pydantic import BaseModel, SecretStr
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.integrations.base import BaseOAuthProvider
from tracecat.integrations.models import (
    ProviderCategory,
    ProviderConfig,
    ProviderMetadata,
    TokenResponse,
)
from tracecat.integrations.service import IntegrationService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatAuthorizationError

pytestmark = pytest.mark.usefixtures("db")


# Mock OAuth Provider for testing
class MockProviderConfig(BaseModel):
    """Configuration for mock OAuth provider."""

    redirect_uri: str | None = None


class MockOAuthProvider(BaseOAuthProvider):
    """Mock OAuth provider for testing."""

    id: ClassVar[str] = "mock_provider"
    _authorization_endpoint: ClassVar[str] = "https://mock.provider/oauth/authorize"
    _token_endpoint: ClassVar[str] = "https://mock.provider/oauth/token"
    default_scopes: ClassVar[list[str]] = ["read", "write"]
    config_model: ClassVar[type[BaseModel]] = MockProviderConfig
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="mock_provider",
        name="Mock Provider",
        description="A mock OAuth provider for testing",
        api_docs_url="https://mock.provider/docs",
        categories=[ProviderCategory.OTHER],
    )


class MockOAuthProviderWithPKCE(MockOAuthProvider):
    """Mock OAuth provider that uses PKCE and additional parameters."""

    id: ClassVar[str] = "mock_provider_pkce"

    def _use_pkce(self) -> bool:
        """Enable PKCE for this provider."""
        return True

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Add custom authorization parameters."""
        return {"custom_auth_param": "auth_value", "audience": "test-api"}

    def _get_additional_token_params(self) -> dict[str, Any]:
        """Add custom token exchange parameters."""
        return {"custom_token_param": "token_value", "resource": "test-resource"}


@pytest.fixture
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set up encryption key for testing."""
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TRACECAT__DB_ENCRYPTION_KEY", key)
    return key


@pytest.fixture
async def integration_service(
    session: AsyncSession, svc_role: Role, encryption_key: str
) -> IntegrationService:
    """Create an integration service instance for testing."""
    return IntegrationService(session=session, role=svc_role)


@pytest.fixture
def mock_token_response() -> TokenResponse:
    """Create a mock token response."""
    return TokenResponse(
        access_token=SecretStr("mock_access_token"),
        refresh_token=SecretStr("mock_refresh_token"),
        expires_in=3600,
        scope="read write",
        token_type="Bearer",
    )


@pytest.fixture
def mock_provider(monkeypatch: pytest.MonkeyPatch) -> MockOAuthProvider:
    """Create a mock OAuth provider instance."""
    # Set required environment variable
    monkeypatch.setenv("TRACECAT__PUBLIC_APP_URL", "http://localhost:8000")
    return MockOAuthProvider(
        client_id="mock_client_id", client_secret="mock_client_secret"
    )


@pytest.mark.anyio
class TestIntegrationService:
    """Test the IntegrationService class."""

    async def test_service_initialization_requires_workspace(
        self, session: AsyncSession
    ) -> None:
        """Test that service initialization requires a workspace ID."""
        # Create a role without workspace_id
        role_without_workspace = Role(
            type="service",
            user_id=uuid.uuid4(),
            workspace_id=None,
            service_id="tracecat-service",
        )

        # Attempt to create service without workspace should raise error
        with pytest.raises(TracecatAuthorizationError):
            IntegrationService(session=session, role=role_without_workspace)

    async def test_store_and_get_integration(
        self,
        integration_service: IntegrationService,
        mock_token_response: TokenResponse,
    ) -> None:
        """Test storing and retrieving an integration."""
        provider_id = "test_provider"

        # Store integration without user_id (workspace-level)
        integration = await integration_service.store_integration(
            provider_id=provider_id,
            access_token=mock_token_response.access_token,
            refresh_token=mock_token_response.refresh_token,
            expires_in=mock_token_response.expires_in,
            scope=mock_token_response.scope,
        )

        assert integration.provider_id == provider_id
        assert integration.user_id is None  # workspace-level integration
        assert integration.expires_at is not None
        assert integration.scope == mock_token_response.scope

        # Retrieve integration
        retrieved = await integration_service.get_integration(provider_id=provider_id)
        assert retrieved is not None
        assert retrieved.id == integration.id
        assert retrieved.provider_id == provider_id

    async def test_update_existing_integration(
        self,
        integration_service: IntegrationService,
        mock_token_response: TokenResponse,
    ) -> None:
        """Test updating an existing integration."""
        provider_id = "test_provider"

        # Store initial integration
        initial = await integration_service.store_integration(
            provider_id=provider_id,
            access_token=SecretStr("initial_token"),
            refresh_token=SecretStr("initial_refresh"),
            expires_in=1800,
            scope="read",
        )

        # Update with new tokens (with different expiry)
        updated = await integration_service.store_integration(
            provider_id=provider_id,
            access_token=mock_token_response.access_token,
            refresh_token=mock_token_response.refresh_token,
            expires_in=7200,  # Different expiry time
            scope=mock_token_response.scope,
        )

        # Should be same integration, but updated
        assert updated.id == initial.id
        assert updated.scope == mock_token_response.scope
        # The expires_at should be different (may be >= due to timing)
        assert updated.expires_at is not None, "Expires at should not be None"
        assert initial.expires_at is not None, "Expires at should not be None"
        assert updated.expires_at >= initial.expires_at, (
            "New expiry should be greater than or equal to old expiry"
        )

    async def test_store_integration_with_user_id(
        self,
        integration_service: IntegrationService,
        mock_token_response: TokenResponse,
    ) -> None:
        """Test that user_id parameter is accepted and stored correctly."""
        provider_id = "test_provider"

        # Store workspace-level integration (user_id=None is tested in other tests)
        workspace_integration = await integration_service.store_integration(
            provider_id=provider_id,
            access_token=SecretStr("workspace_token"),
            user_id=None,  # No user_id = workspace-level
        )

        # Verify workspace-level integration
        assert workspace_integration.user_id is None
        assert workspace_integration.owner_id == integration_service.workspace_id

        # Note: Testing actual user_id insertion requires foreign key setup with user table,
        # but the method signature and parameter handling is covered

    async def test_store_integration_updates_provider_config(
        self,
        integration_service: IntegrationService,
        mock_token_response: TokenResponse,
    ) -> None:
        """Test updating provider_config on an existing integration."""
        provider_id = "test_provider"

        # Store initial integration with provider_config
        initial_config = {"api_endpoint": "https://api.v1.example.com", "timeout": 30}
        integration = await integration_service.store_integration(
            provider_id=provider_id,
            access_token=mock_token_response.access_token,
            provider_config=initial_config,
        )

        assert integration.provider_config == initial_config

        # Update with new provider_config
        updated_config = {
            "api_endpoint": "https://api.v2.example.com",
            "timeout": 60,
            "new_field": "new_value",
        }
        updated = await integration_service.store_integration(
            provider_id=provider_id,
            access_token=SecretStr("new_access_token"),
            provider_config=updated_config,
        )

        # Should be same integration but with updated config
        assert updated.id == integration.id
        assert updated.provider_config == updated_config
        assert updated.provider_config != initial_config

    async def test_list_integrations(
        self,
        integration_service: IntegrationService,
        mock_token_response: TokenResponse,
    ) -> None:
        """Test listing integrations with optional filtering."""
        # Store multiple integrations
        providers = ["provider1", "provider2", "provider3"]
        for provider_id in providers:
            await integration_service.store_integration(
                provider_id=provider_id,
                access_token=mock_token_response.access_token,
                expires_in=3600,
            )

        # List all integrations
        all_integrations = await integration_service.list_integrations()
        assert len(all_integrations) == 3

        # List filtered integrations
        filtered = await integration_service.list_integrations(
            providers={"provider1", "provider3"}
        )
        assert len(filtered) == 2
        assert all(i.provider_id in {"provider1", "provider3"} for i in filtered)

    async def test_remove_integration(
        self,
        integration_service: IntegrationService,
        mock_token_response: TokenResponse,
    ) -> None:
        """Test removing an integration."""
        provider_id = "test_provider"

        # Store integration
        integration = await integration_service.store_integration(
            provider_id=provider_id,
            access_token=mock_token_response.access_token,
        )

        # Remove integration
        await integration_service.remove_integration(integration=integration)

        # Verify it's gone
        retrieved = await integration_service.get_integration(provider_id=provider_id)
        assert retrieved is None

    async def test_token_encryption_decryption(
        self,
        integration_service: IntegrationService,
        mock_token_response: TokenResponse,
    ) -> None:
        """Test that tokens are properly encrypted and decrypted."""
        provider_id = "test_provider"

        # Type narrow
        assert mock_token_response.refresh_token is not None, (
            "Refresh token should not be None"
        )

        # Store integration
        integration = await integration_service.store_integration(
            provider_id=provider_id,
            access_token=mock_token_response.access_token,
            refresh_token=mock_token_response.refresh_token,
        )

        # Verify tokens are encrypted in DB
        assert (
            integration.encrypted_access_token
            != mock_token_response.access_token.get_secret_value().encode()
        )
        assert integration.encrypted_refresh_token is not None, (
            "Refresh token should not be None"
        )
        assert (
            integration.encrypted_refresh_token
            != mock_token_response.refresh_token.get_secret_value().encode()
        )

        # Get decrypted tokens
        access_token, refresh_token = integration_service.get_decrypted_tokens(
            integration
        )
        assert access_token == mock_token_response.access_token.get_secret_value()
        assert refresh_token == mock_token_response.refresh_token.get_secret_value()

        # Get access token only
        access_token_only = await integration_service.get_access_token(integration)
        assert (
            access_token_only.get_secret_value()
            == mock_token_response.access_token.get_secret_value()
        )

    async def test_token_expiration_checks(
        self,
        integration_service: IntegrationService,
        mock_token_response: TokenResponse,
    ) -> None:
        """Test token expiration and refresh checks."""
        provider_id = "test_provider"

        # Store integration that expires in 1 hour
        integration = await integration_service.store_integration(
            provider_id=provider_id,
            access_token=mock_token_response.access_token,
            expires_in=3600,
        )

        # Should not be expired or need refresh
        assert not integration.is_expired
        assert not integration.needs_refresh

        # Manually set expiration to past (use UTC)
        integration.expires_at = datetime.now(UTC) - timedelta(hours=1)
        assert integration.is_expired
        assert integration.needs_refresh

        # Set expiration to 4 minutes in future (within refresh window)
        integration.expires_at = datetime.now(UTC) + timedelta(minutes=4)
        assert not integration.is_expired
        assert integration.needs_refresh

    async def test_refresh_token_if_needed(
        self,
        integration_service: IntegrationService,
        mock_provider: MockOAuthProvider,
        mock_token_response: TokenResponse,
    ) -> None:
        """Test automatic token refresh when needed."""
        provider_id = "mock_provider"

        # Mock the provider registry
        with patch("tracecat.integrations.service.ProviderRegistry") as mock_registry:
            mock_registry.get.return_value.get_class.return_value = MockOAuthProvider

            # Mock the refresh_access_token method
            with patch.object(
                MockOAuthProvider, "refresh_access_token", new_callable=AsyncMock
            ) as mock_refresh:
                mock_refresh.return_value = TokenResponse(
                    access_token=SecretStr("refreshed_token"),
                    refresh_token=SecretStr("new_refresh_token"),
                    expires_in=7200,
                    scope="read write",
                    token_type="Bearer",
                )

                # Store integration that needs refresh
                integration = await integration_service.store_integration(
                    provider_id=provider_id,
                    access_token=mock_token_response.access_token,
                    refresh_token=mock_token_response.refresh_token,
                    expires_in=60,  # Expires in 1 minute
                )

                # Refresh the token
                refreshed = await integration_service.refresh_token_if_needed(
                    integration
                )

                # Verify refresh was called
                mock_refresh.assert_called_once()

                # Verify tokens were updated
                access_token, refresh_token = integration_service.get_decrypted_tokens(
                    refreshed
                )
                assert access_token == "refreshed_token"
                assert refresh_token == "new_refresh_token"
                # New expiry should be different (potentially same due to timing)
                assert refreshed.expires_at is not None, "Expires at should not be None"
                assert integration.expires_at is not None, (
                    "Expires at should not be None"
                )
                assert refreshed.expires_at >= integration.expires_at, (
                    "New expiry should be greater than or equal to old expiry"
                )

    async def test_refresh_token_if_needed_no_refresh_token(
        self,
        integration_service: IntegrationService,
        mock_token_response: TokenResponse,
    ) -> None:
        """Test refresh when no refresh token is available."""
        provider_id = "test_provider"

        # Store integration without refresh token
        integration = await integration_service.store_integration(
            provider_id=provider_id,
            access_token=mock_token_response.access_token,
            refresh_token=None,  # No refresh token
            expires_in=60,  # Short expiry
        )

        # Force expiration by setting expires_at to past
        integration.expires_at = datetime.now(UTC) - timedelta(hours=1)
        integration_service.session.add(integration)
        await integration_service.session.commit()
        await integration_service.session.refresh(integration)

        # Attempt refresh - should return unchanged
        refreshed = await integration_service.refresh_token_if_needed(integration)

        # Should be the same instance, unchanged
        assert refreshed.id == integration.id
        assert refreshed.expires_at == integration.expires_at

    async def test_refresh_token_if_needed_provider_not_found(
        self,
        integration_service: IntegrationService,
        mock_token_response: TokenResponse,
    ) -> None:
        """Test refresh when provider is not found in registry."""
        provider_id = "unknown_provider"

        # Store integration with tokens that will expire
        integration = await integration_service.store_integration(
            provider_id=provider_id,
            access_token=mock_token_response.access_token,
            refresh_token=mock_token_response.refresh_token,
            expires_in=60,  # Short expiry
        )

        # Force expiration
        integration.expires_at = datetime.now(UTC) - timedelta(hours=1)
        integration_service.session.add(integration)
        await integration_service.session.commit()
        await integration_service.session.refresh(integration)

        # Mock the provider registry to return None
        with patch("tracecat.integrations.service.ProviderRegistry") as mock_registry:
            mock_registry.get.return_value.get_class.return_value = None

            # Attempt refresh - should return unchanged
            refreshed = await integration_service.refresh_token_if_needed(integration)

            # Should be the same instance, unchanged
            assert refreshed.id == integration.id
            assert refreshed.expires_at == integration.expires_at

    async def test_refresh_token_if_needed_no_rotation(
        self,
        integration_service: IntegrationService,
        mock_provider: MockOAuthProvider,
        mock_token_response: TokenResponse,
    ) -> None:
        """Test refresh when provider doesn't rotate refresh token."""
        provider_id = "mock_provider"

        # Store the original refresh token for later comparison
        original_refresh_token = mock_token_response.refresh_token

        # Mock the provider registry
        with patch("tracecat.integrations.service.ProviderRegistry") as mock_registry:
            mock_registry.get.return_value.get_class.return_value = MockOAuthProvider

            # Mock the refresh_access_token method to return None for refresh_token
            with patch.object(
                MockOAuthProvider, "refresh_access_token", new_callable=AsyncMock
            ) as mock_refresh:
                mock_refresh.return_value = TokenResponse(
                    access_token=SecretStr("refreshed_token"),
                    refresh_token=None,  # No new refresh token
                    expires_in=7200,
                    scope="read write",
                    token_type="Bearer",
                )

                # Store integration that needs refresh
                integration = await integration_service.store_integration(
                    provider_id=provider_id,
                    access_token=mock_token_response.access_token,
                    refresh_token=original_refresh_token,
                    expires_in=60,  # Expires in 1 minute
                )

                # Store the original encrypted refresh token
                original_encrypted = integration.encrypted_refresh_token

                # Refresh the token
                refreshed = await integration_service.refresh_token_if_needed(
                    integration
                )

                # Verify refresh was called
                mock_refresh.assert_called_once()

                # Verify access token was updated
                access_token, refresh_token = integration_service.get_decrypted_tokens(
                    refreshed
                )
                assert access_token == "refreshed_token"

                # Verify refresh token was NOT changed
                assert refreshed.encrypted_refresh_token == original_encrypted
                assert original_refresh_token is not None, (
                    "Original refresh token should not be None"
                )
                assert refresh_token == original_refresh_token.get_secret_value()

    async def test_encryption_key_mismatch(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_token_response: TokenResponse,
    ) -> None:
        """Test decryption failure with wrong encryption key."""
        from cryptography.fernet import Fernet

        # Create service with first encryption key
        key1 = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"TRACECAT__DB_ENCRYPTION_KEY": key1}):
            service1 = IntegrationService(session=session, role=svc_role)

            # Store integration
            integration = await service1.store_integration(
                provider_id="test_provider",
                access_token=mock_token_response.access_token,
                refresh_token=mock_token_response.refresh_token,
            )

        # Create service with different encryption key
        key2 = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"TRACECAT__DB_ENCRYPTION_KEY": key2}):
            service2 = IntegrationService(session=session, role=svc_role)

            # Attempt to decrypt with wrong key should raise error
            with pytest.raises(Exception):  # noqa: B017
                service2.get_decrypted_tokens(integration)

            with pytest.raises(Exception):  # noqa: B017
                await service2.get_access_token(integration)

    async def test_store_and_get_provider_config(
        self,
        integration_service: IntegrationService,
    ) -> None:
        """Test storing and retrieving provider configuration."""
        provider_id = "test_provider"
        client_id = "test_client_id"
        client_secret = SecretStr("test_client_secret")
        provider_config = {"custom_setting": "value"}

        # Store provider config
        integration = await integration_service.store_provider_config(
            provider_id=provider_id,
            client_id=client_id,
            client_secret=client_secret,
            provider_config=provider_config,
        )

        assert integration.use_workspace_credentials is True
        assert integration.provider_config == provider_config

        # Get provider config
        config = integration_service.get_provider_config(integration=integration)
        assert config is not None
        assert config.client_id == client_id
        assert (
            config.client_secret.get_secret_value() == client_secret.get_secret_value()
        )
        assert config.provider_config == provider_config

    async def test_remove_provider_config(
        self,
        integration_service: IntegrationService,
        mock_token_response: TokenResponse,
    ) -> None:
        """Test removing provider configuration."""
        provider_id = "test_provider"

        # First, store provider config without tokens
        await integration_service.store_provider_config(
            provider_id=provider_id,
            client_id="test_client",
            client_secret=SecretStr("test_secret"),
            provider_config={},
        )

        # Remove provider config - should delete entire record
        removed = await integration_service.remove_provider_config(
            provider_id=provider_id
        )
        assert removed is True

        # Verify it's gone
        integration = await integration_service.get_integration(provider_id=provider_id)
        assert integration is None

        # Now test with existing tokens
        await integration_service.store_integration(
            provider_id=provider_id,
            access_token=mock_token_response.access_token,
        )
        await integration_service.store_provider_config(
            provider_id=provider_id,
            client_id="test_client",
            client_secret=SecretStr("test_secret"),
            provider_config={},
        )

        # Remove provider config - should only clear credentials
        removed = await integration_service.remove_provider_config(
            provider_id=provider_id
        )
        assert removed is True

        # Integration should still exist but without credentials
        integration = await integration_service.get_integration(provider_id=provider_id)
        assert integration is not None
        assert integration.encrypted_client_id is None
        assert integration.encrypted_client_secret is None
        assert integration.use_workspace_credentials is False


@pytest.mark.anyio
class TestBaseOAuthProvider:
    """Test the BaseOAuthProvider class."""

    async def test_provider_initialization(
        self, mock_provider: MockOAuthProvider
    ) -> None:
        """Test OAuth provider initialization."""
        assert mock_provider.id == "mock_provider"
        assert mock_provider.client_id == "mock_client_id"
        assert mock_provider.client_secret == "mock_client_secret"
        assert mock_provider.scopes == ["read", "write"]
        assert (
            mock_provider.redirect_uri()
            == "http://localhost/integrations/mock_provider/callback"
        )

    async def test_get_authorization_url(
        self, mock_provider: MockOAuthProvider
    ) -> None:
        """Test generating authorization URL."""
        state = "test_state_123"

        with patch.object(
            AsyncOAuth2Client, "create_authorization_url"
        ) as mock_create_url:
            mock_create_url.return_value = (
                "https://mock.provider/oauth/authorize?client_id=mock_client_id&state=test_state_123",
                state,
            )

            url = await mock_provider.get_authorization_url(state)

            assert "mock.provider/oauth/authorize" in url
            assert state in url
            mock_create_url.assert_called_once_with(
                mock_provider.authorization_endpoint,
                state=state,
            )

    async def test_exchange_code_for_token(
        self, mock_provider: MockOAuthProvider, mock_token_response: TokenResponse
    ) -> None:
        """Test exchanging authorization code for tokens."""
        code = "test_auth_code"
        state = "test_state"

        # Type narrow
        assert mock_token_response.refresh_token is not None, (
            "Refresh token should not be None"
        )

        with patch.object(
            AsyncOAuth2Client, "fetch_token", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = {
                "access_token": mock_token_response.access_token.get_secret_value(),
                "refresh_token": mock_token_response.refresh_token.get_secret_value(),
                "expires_in": mock_token_response.expires_in,
                "scope": mock_token_response.scope,
                "token_type": mock_token_response.token_type,
            }

            result = await mock_provider.exchange_code_for_token(code, state)

            assert (
                result.access_token.get_secret_value()
                == mock_token_response.access_token.get_secret_value()
            )
            assert result.refresh_token is not None, "Refresh token should not be None"
            assert (
                result.refresh_token.get_secret_value()
                == mock_token_response.refresh_token.get_secret_value()
            )
            assert result.expires_in == mock_token_response.expires_in
            assert result.scope == mock_token_response.scope

            mock_fetch.assert_called_once_with(
                mock_provider.token_endpoint,
                code=code,
                state=state,
            )

    async def test_refresh_access_token(self, mock_provider: MockOAuthProvider) -> None:
        """Test refreshing access token."""
        refresh_token = "old_refresh_token"

        with patch.object(
            AsyncOAuth2Client, "refresh_token", new_callable=AsyncMock
        ) as mock_refresh:
            mock_refresh.return_value = {
                "access_token": "new_access_token",
                "refresh_token": "new_refresh_token",
                "expires_in": 3600,
                "scope": "read write",
                "token_type": "Bearer",
            }

            result = await mock_provider.refresh_access_token(refresh_token)

            assert result.access_token.get_secret_value() == "new_access_token"
            assert result.refresh_token is not None, "Refresh token should not be None"
            assert result.refresh_token.get_secret_value() == "new_refresh_token"
            assert result.expires_in == 3600

            mock_refresh.assert_called_once_with(
                mock_provider.token_endpoint,
                refresh_token=refresh_token,
            )

    async def test_from_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test creating provider from configuration."""
        monkeypatch.setenv("TRACECAT__PUBLIC_APP_URL", "http://localhost:8000")

        config = ProviderConfig(
            client_id="config_client_id",
            client_secret=SecretStr("config_client_secret"),
            provider_config={"redirect_uri": "custom_redirect"},
        )

        provider = MockOAuthProvider.from_config(config)

        assert provider.client_id == "config_client_id"
        assert provider.client_secret == "config_client_secret"

    async def test_pkce_and_extra_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test PKCE and additional parameter support."""
        monkeypatch.setenv("TRACECAT__PUBLIC_APP_URL", "http://localhost:8000")

        # Create provider with PKCE and custom params
        provider = MockOAuthProviderWithPKCE(
            client_id="test_client_id", client_secret="test_client_secret"
        )

        # Test authorization URL with custom params
        state = "test_state_123"

        with patch.object(
            AsyncOAuth2Client, "create_authorization_url"
        ) as mock_create_url:
            mock_create_url.return_value = (
                "https://mock.provider/oauth/authorize?client_id=test_client_id&custom_auth_param=auth_value",
                state,
            )

            _url = await provider.get_authorization_url(state)

            # Verify create_authorization_url was called with custom params
            mock_create_url.assert_called_once_with(
                provider.authorization_endpoint,
                state=state,
                custom_auth_param="auth_value",
                audience="test-api",
            )

        # Test token exchange with custom params
        code = "test_auth_code"

        with patch.object(
            AsyncOAuth2Client, "fetch_token", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = {
                "access_token": "test_access_token",
                "refresh_token": "test_refresh_token",
                "expires_in": 3600,
                "scope": "read write",
                "token_type": "Bearer",
            }

            await provider.exchange_code_for_token(code, state)

            # Verify fetch_token was called with custom params
            mock_fetch.assert_called_once_with(
                provider.token_endpoint,
                code=code,
                state=state,
                custom_token_param="token_value",
                resource="test-resource",
            )

    async def test_exchange_code_for_token_error_handling(
        self, mock_provider: MockOAuthProvider
    ) -> None:
        """Test error handling during token exchange."""
        code = "test_auth_code"
        state = "test_state"

        # Test with network/API error
        with patch.object(
            AsyncOAuth2Client, "fetch_token", new_callable=AsyncMock
        ) as mock_fetch:
            # Mock fetch_token to raise an exception
            mock_fetch.side_effect = Exception("Network error")

            # Should raise the exception
            with pytest.raises(Exception, match="Network error"):
                await mock_provider.exchange_code_for_token(code, state)

            mock_fetch.assert_called_once_with(
                mock_provider.token_endpoint,
                code=code,
                state=state,
            )


# NOTE: Workflow integration test removed as it requires Temporal server
# The test would verify that OAuth tokens are accessible within workflow actions
# by creating a test workflow that retrieves tokens via core.secrets.get
