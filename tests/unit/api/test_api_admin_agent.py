"""HTTP-level tests for admin agent API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import tracecat.admin.agent.router as admin_agent_router_module
from tracecat.admin.agent.schemas import PlatformCatalogEntry, PlatformCatalogRead
from tracecat.agent.types import ModelDiscoveryStatus
from tracecat.auth.types import Role


@pytest.mark.anyio
async def test_list_platform_catalog_success(
    client: TestClient, test_admin_role: Role
) -> None:
    payload = PlatformCatalogRead(
        discovery_status=ModelDiscoveryStatus.READY,
        last_refreshed_at=datetime(2024, 1, 1, tzinfo=UTC),
        last_error=None,
        next_cursor=None,
        models=[
            PlatformCatalogEntry(
                id=uuid.uuid4(),
                model_provider="openai",
                model_name="gpt-5",
                metadata={"tier": "platform"},
            )
        ],
    )

    with patch.object(
        admin_agent_router_module, "AdminAgentService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_platform_catalog.return_value = payload
        mock_service_cls.return_value = mock_svc

        response = client.get("/admin/agent/catalog/platform?query=gpt&provider=openai")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["discovery_status"] == "ready"
    assert data["models"][0]["model_provider"] == "openai"
    assert data["models"][0]["model_name"] == "gpt-5"
    assert uuid.UUID(data["models"][0]["id"])
    mock_svc.list_platform_catalog.assert_awaited_once_with(
        query="gpt",
        provider="openai",
        cursor=None,
        limit=100,
    )


@pytest.mark.anyio
async def test_refresh_platform_catalog_success(
    client: TestClient, test_admin_role: Role
) -> None:
    payload = PlatformCatalogRead(
        discovery_status=ModelDiscoveryStatus.READY,
        last_refreshed_at=datetime(2024, 1, 1, tzinfo=UTC),
        last_error=None,
        next_cursor=None,
        models=[
            PlatformCatalogEntry(
                id=uuid.uuid4(),
                model_provider="anthropic",
                model_name="claude-sonnet-4-5",
                metadata=None,
            )
        ],
    )

    with patch.object(
        admin_agent_router_module, "AdminAgentService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.refresh_platform_catalog.return_value = payload
        mock_service_cls.return_value = mock_svc

        response = client.post("/admin/agent/catalog/platform/refresh")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["models"][0]["model_provider"] == "anthropic"
    mock_svc.refresh_platform_catalog.assert_awaited_once_with()


@pytest.mark.anyio
async def test_list_platform_catalog_invalid_cursor_returns_bad_request(
    client: TestClient, test_admin_role: Role
) -> None:
    with patch.object(
        admin_agent_router_module, "AdminAgentService"
    ) as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_platform_catalog.side_effect = ValueError(
            "Invalid cursor. Expected a non-negative integer offset."
        )
        mock_service_cls.return_value = mock_svc

        response = client.get("/admin/agent/catalog/platform?cursor=abc")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json() == {
        "detail": "Invalid cursor. Expected a non-negative integer offset."
    }
