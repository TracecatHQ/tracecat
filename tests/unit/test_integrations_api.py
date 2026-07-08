"""API-level tests for integrations endpoints."""

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.integrations.enums import IntegrationStatus, OAuthGrantType
from tracecat.integrations.schemas import ProviderKey
from tracecat.integrations.service import (
    IntegrationService,
    ReauthorizationRequiredError,
)

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
class TestIntegrationsAPI:
    """Test integrations API endpoints.

    NOTE: These tests verify the service layer behavior.
    Full API endpoint tests require a running server with proper middleware.

    The integrations created by ``store_provider_config`` alone are only
    CONFIGURED (no access token stored), so eager handshake writes still apply
    to them. The reauthorization/409 path is exercised on CONNECTED
    authorization-code integrations in ``TestIntegrationsReauthorizationAPI``.
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


@pytest.mark.anyio
class TestIntegrationsReauthorizationAPI:
    """The PUT provider-config path must reject handshake changes on a
    CONNECTED authorization-code integration with a machine-readable
    ``reauth_required`` error (surfaced as HTTP 409 by the router).
    """

    async def _make_connected_ac_integration(
        self,
        service: IntegrationService,
        provider_key: ProviderKey,
        *,
        client_id: str = "live_client_id",
        scopes: list[str],
    ) -> None:
        await service.store_provider_config(
            provider_key=provider_key,
            client_id=client_id,
            client_secret=SecretStr("live_secret"),
            requested_scopes=scopes,
        )
        integration = await service.store_integration(
            provider_key=provider_key,
            access_token=SecretStr("live_access_token"),
            refresh_token=SecretStr("live_refresh_token"),
            expires_in=3600,
            scope=" ".join(scopes),
        )
        assert integration.status == IntegrationStatus.CONNECTED

    # (a) Changed scopes on a CONNECTED AC integration -> reauth_required.
    async def test_put_changed_scopes_on_connected_requires_reauth(
        self,
        session: AsyncSession,
        svc_role: Role,
    ) -> None:
        service = IntegrationService(session=session, role=svc_role)
        provider_key = ProviderKey(
            id="microsoft_graph", grant_type=OAuthGrantType.AUTHORIZATION_CODE
        )
        await self._make_connected_ac_integration(
            service, provider_key, scopes=["read", "write"]
        )

        with pytest.raises(ReauthorizationRequiredError) as exc_info:
            await service.store_provider_config(
                provider_key=provider_key,
                requested_scopes=["read", "write", "admin"],
            )
        assert exc_info.value.code == "reauth_required"
        assert "scopes" in exc_info.value.changed_fields

        # Live scopes untouched by the rejected eager write.
        integrations = await service.list_integrations(provider_keys={provider_key})
        assert len(integrations) == 1
        assert integrations[0].requested_scopes == "read write"

    # (f) Unchanged handshake values on a CONNECTED AC integration -> no error.
    async def test_put_unchanged_scopes_on_connected_succeeds(
        self,
        session: AsyncSession,
        svc_role: Role,
    ) -> None:
        service = IntegrationService(session=session, role=svc_role)
        provider_key = ProviderKey(
            id="microsoft_graph", grant_type=OAuthGrantType.AUTHORIZATION_CODE
        )
        await self._make_connected_ac_integration(
            service, provider_key, client_id="stable_id", scopes=["read", "write"]
        )

        # Re-submitting the exact current config must not raise.
        updated = await service.store_provider_config(
            provider_key=provider_key,
            client_id="stable_id",
            requested_scopes=["read", "write"],
        )
        assert updated.status == IntegrationStatus.CONNECTED
        assert updated.requested_scopes == "read write"

    # Not-connected eager coverage: a CONFIGURED (not CONNECTED) integration
    # still accepts eager scope writes.
    async def test_put_changed_scopes_on_not_connected_is_eager(
        self,
        session: AsyncSession,
        svc_role: Role,
    ) -> None:
        service = IntegrationService(session=session, role=svc_role)
        provider_key = ProviderKey(
            id="microsoft_graph", grant_type=OAuthGrantType.AUTHORIZATION_CODE
        )
        # Only client credentials stored -> CONFIGURED, not CONNECTED.
        configured = await service.store_provider_config(
            provider_key=provider_key,
            client_id="cfg_client_id",
            client_secret=SecretStr("cfg_secret"),
            requested_scopes=["read", "write"],
        )
        assert configured.status == IntegrationStatus.CONFIGURED

        updated = await service.store_provider_config(
            provider_key=provider_key,
            requested_scopes=["read", "write", "admin"],
        )
        assert updated.requested_scopes == "read write admin"
