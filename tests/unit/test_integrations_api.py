"""API-level tests for integrations endpoints."""

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.models import ProviderKey
from tracecat.integrations.service import IntegrationService
from tracecat.types.auth import Role

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def authenticated_client(
    client: AsyncClient, test_user_auth_headers: dict[str, str]
) -> AsyncClient:
    """Return client with authentication headers."""
    client.headers.update(test_user_auth_headers)
    return client


@pytest.mark.anyio
class TestIntegrationsAPI:
    """Test integrations API endpoints."""

    async def test_update_integration_empty_scopes(
        self,
        authenticated_client: AsyncClient,
        session: AsyncSession,
        test_role: Role,
    ) -> None:
        """Test updating integration with empty scopes array."""
        # First create an integration
        service = IntegrationService(session=session, role=test_role)
        provider_key = ProviderKey(
            id="microsoft_graph", grant_type=OAuthGrantType.AUTHORIZATION_CODE
        )

        integration = await service.store_provider_config(
            provider_key=provider_key,
            client_id="test_client_id",
            client_secret=SecretStr("test_secret"),
            requested_scopes=["read", "write"],
        )

        # Update via API with empty scopes
        response = await authenticated_client.patch(
            f"/integrations/{integration.id}",
            json={
                "scopes": [],
                "grant_type": "authorization_code",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["requested_scopes"] == []

    async def test_update_integration_custom_scopes(
        self,
        authenticated_client: AsyncClient,
        session: AsyncSession,
        test_role: Role,
    ) -> None:
        """Test updating integration with custom scopes."""
        # First create an integration
        service = IntegrationService(session=session, role=test_role)
        provider_key = ProviderKey(
            id="microsoft_graph", grant_type=OAuthGrantType.AUTHORIZATION_CODE
        )

        integration = await service.store_provider_config(
            provider_key=provider_key,
            client_id="test_client_id",
            client_secret=SecretStr("test_secret"),
            requested_scopes=["read", "write"],
        )

        # Update via API with custom scopes
        response = await authenticated_client.patch(
            f"/integrations/{integration.id}",
            json={
                "scopes": ["custom.read", "custom.write", "admin"],
                "grant_type": "authorization_code",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["requested_scopes"] == ["custom.read", "custom.write", "admin"]

    async def test_update_integration_scopes_persistence(
        self,
        authenticated_client: AsyncClient,
        session: AsyncSession,
        test_role: Role,
    ) -> None:
        """Test that scope updates persist across requests."""
        # First create an integration
        service = IntegrationService(session=session, role=test_role)
        provider_key = ProviderKey(
            id="microsoft_graph", grant_type=OAuthGrantType.AUTHORIZATION_CODE
        )

        integration = await service.store_provider_config(
            provider_key=provider_key,
            client_id="test_client_id",
            client_secret=SecretStr("test_secret"),
            requested_scopes=["read", "write"],
        )

        # Update with custom scopes
        response = await authenticated_client.patch(
            f"/integrations/{integration.id}",
            json={
                "scopes": ["scope1", "scope2"],
                "grant_type": "authorization_code",
            },
        )
        assert response.status_code == 200

        # Fetch integration again
        response = await authenticated_client.get(f"/integrations/{integration.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["requested_scopes"] == ["scope1", "scope2"]

        # Update with empty scopes
        response = await authenticated_client.patch(
            f"/integrations/{integration.id}",
            json={
                "scopes": [],
                "grant_type": "authorization_code",
            },
        )
        assert response.status_code == 200

        # Fetch again to verify empty
        response = await authenticated_client.get(f"/integrations/{integration.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["requested_scopes"] == []

    async def test_provider_list_no_categories(
        self,
        authenticated_client: AsyncClient,
    ) -> None:
        """Test that provider list endpoint doesn't include categories field."""
        response = await authenticated_client.get("/providers")

        assert response.status_code == 200
        data = response.json()

        # Check that providers exist
        assert len(data) > 0

        # Verify no provider has categories field
        for provider in data:
            assert "categories" not in provider
            # Verify expected fields exist
            assert "id" in provider
            assert "name" in provider
            assert "description" in provider
            assert "grant_type" in provider
            assert "integration_status" in provider
