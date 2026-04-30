"""HTTP-level tests for admin agent API endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import tracecat.admin.agent.router as admin_agent_router
from tracecat.agent.catalog.schemas import AgentCatalogRead
from tracecat.auth.types import Role


@pytest.mark.anyio
async def test_list_platform_catalog_success(
    client: TestClient, test_admin_role: Role
) -> None:
    catalog_item = AgentCatalogRead(
        id=uuid.uuid4(),
        custom_provider_id=None,
        organization_id=None,
        model_provider="openai",
        model_name="gpt-4.1",
        model_metadata={},
    )

    with patch.object(admin_agent_router, "AgentCatalogService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_platform_catalog.return_value = ([catalog_item], None)
        MockService.return_value = mock_svc

        response = client.get("/admin/agent/catalog")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["items"][0]["model_name"] == "gpt-4.1"
    mock_svc.list_platform_catalog.assert_called_once()
