"""API-level tests for integrations endpoints."""

import pytest
from pydantic import SecretStr
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.schemas import ProviderKey
from tracecat.integrations.service import IntegrationService
from tracecat.types.auth import Role

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
class TestIntegrationsAPI:
    """Test integrations API endpoints.

    NOTE: These tests verify the service layer behavior.
    Full API endpoint tests require a running server with proper middleware.
    """

    async def test_update_integration_empty_scopes(
        self,
        session: AsyncSession,
        svc_role: Role,
    ) -> None:
        """Test updating integration with empty scopes array."""
        # Test at service layer - API layer requires running server
        service = IntegrationService(session=session, role=svc_role)
        provider_key = ProviderKey(
            id="microsoft_graph", grant_type=OAuthGrantType.AUTHORIZATION_CODE
        )

        # Create integration
        await service.store_provider_config(
            provider_key=provider_key,
            client_id="test_client_id",
            client_secret=SecretStr("test_secret"),
            requested_scopes=["read", "write"],
        )

        # Update with empty scopes
        updated = await service.store_provider_config(
            provider_key=provider_key,
            client_id="test_client_id",
            client_secret=SecretStr("test_secret"),
            requested_scopes=[],  # Empty scopes should be respected
        )

        assert updated.requested_scopes == ""  # Empty list is stored as empty string

    async def test_update_integration_custom_scopes(
        self,
        session: AsyncSession,
        svc_role: Role,
    ) -> None:
        """Test updating integration with custom scopes."""
        service = IntegrationService(session=session, role=svc_role)
        provider_key = ProviderKey(
            id="microsoft_graph", grant_type=OAuthGrantType.AUTHORIZATION_CODE
        )

        # Create integration
        await service.store_provider_config(
            provider_key=provider_key,
            client_id="test_client_id",
            client_secret=SecretStr("test_secret"),
            requested_scopes=["read", "write"],
        )

        # Update with custom scopes
        updated = await service.store_provider_config(
            provider_key=provider_key,
            client_id="test_client_id",
            client_secret=SecretStr("test_secret"),
            requested_scopes=["custom.read", "custom.write", "admin"],
        )

        assert (
            updated.requested_scopes == "custom.read custom.write admin"
        )  # Stored as space-separated string

    async def test_update_integration_scopes_persistence(
        self,
        session: AsyncSession,
        svc_role: Role,
    ) -> None:
        """Test that scope updates persist across requests."""
        service = IntegrationService(session=session, role=svc_role)
        provider_key = ProviderKey(
            id="microsoft_graph", grant_type=OAuthGrantType.AUTHORIZATION_CODE
        )

        # Create integration
        await service.store_provider_config(
            provider_key=provider_key,
            client_id="test_client_id",
            client_secret=SecretStr("test_secret"),
            requested_scopes=["read", "write"],
        )

        # Update with custom scopes
        await service.store_provider_config(
            provider_key=provider_key,
            client_id="test_client_id",
            client_secret=SecretStr("test_secret"),
            requested_scopes=["scope1", "scope2"],
        )

        # Fetch and verify
        integrations = await service.list_integrations(provider_keys={provider_key})
        assert len(integrations) == 1
        fetched = integrations[0]
        assert (
            fetched.requested_scopes == "scope1 scope2"
        )  # Stored as space-separated string

        # Update with empty scopes
        await service.store_provider_config(
            provider_key=provider_key,
            client_id="test_client_id",
            client_secret=SecretStr("test_secret"),
            requested_scopes=[],
        )

        # Fetch and verify empty
        integrations = await service.list_integrations(provider_keys={provider_key})
        assert len(integrations) == 1
        fetched = integrations[0]
        assert fetched.requested_scopes == ""  # Empty list is stored as empty string
