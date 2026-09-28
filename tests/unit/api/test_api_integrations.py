"""HTTP-level tests for workspace OAuth integration routes."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.integrations import router as integrations_router
from tracecat.integrations.enums import IntegrationStatus, OAuthGrantType


@pytest.mark.anyio
async def test_list_integrations_distinguishes_grant_types_for_shared_provider(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Integrations sharing a provider id are distinguishable by grant type."""
    user_oauth = SimpleNamespace(
        id=uuid.uuid4(),
        provider_id="google_chronicle",
        grant_type=OAuthGrantType.AUTHORIZATION_CODE,
        status=IntegrationStatus.CONNECTED,
        is_expired=False,
    )
    service_account = SimpleNamespace(
        id=uuid.uuid4(),
        provider_id="google_chronicle",
        grant_type=OAuthGrantType.CLIENT_CREDENTIALS,
        status=IntegrationStatus.CONNECTED,
        is_expired=False,
    )

    with patch.object(integrations_router, "IntegrationService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_integrations.return_value = [user_oauth, service_account]
        mock_service_cls.return_value = mock_svc

        response = client.get(
            "/integrations",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_200_OK
    by_id = {row["id"]: row for row in response.json()}
    assert by_id[str(user_oauth.id)]["grant_type"] == "authorization_code"
    assert by_id[str(service_account.id)]["grant_type"] == "client_credentials"
    assert {row["provider_id"] for row in by_id.values()} == {"google_chronicle"}
