"""HTTP-level tests for agent source API endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

import tracecat.agent.sources.router as agent_sources_router
from tracecat.agent.schemas import (
    AgentModelSourceRead,
    ManualDiscoveredModel,
)
from tracecat.agent.types import CustomModelSourceType, ModelDiscoveryStatus
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError


@pytest.mark.anyio
async def test_list_sources_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    source_id = uuid.uuid4()
    payload = [
        AgentModelSourceRead(
            id=source_id,
            type=CustomModelSourceType.MANUAL_CUSTOM,
            flavor=None,
            display_name="Manual source",
            base_url="https://models.example/v1",
            api_key_configured=True,
            api_key_header="X-Api-Key",
            api_version="2024-06-01",
            discovery_status=ModelDiscoveryStatus.READY,
            last_refreshed_at=None,
            last_error=None,
            declared_models=[
                ManualDiscoveredModel(
                    model_name="qwen2.5:7b",
                    display_name="Qwen 2.5 7B",
                    model_provider="openai_compatible_gateway",
                )
            ],
        )
    ]

    with patch.object(agent_sources_router, "AgentSourceService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.list_model_sources.return_value = payload
        mock_service_cls.return_value = mock_svc

        response = client.get("/agent/sources")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()[0]["display_name"] == "Manual source"
    mock_svc.list_model_sources.assert_awaited_once_with()


@pytest.mark.anyio
async def test_create_source_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    source_id = uuid.uuid4()
    payload = AgentModelSourceRead(
        id=source_id,
        type=CustomModelSourceType.OPENAI_COMPATIBLE_GATEWAY,
        flavor=None,
        display_name="Gateway source",
        base_url="https://gateway.example/v1",
        api_key_configured=False,
        api_key_header=None,
        api_version=None,
        discovery_status=ModelDiscoveryStatus.NEVER,
        last_refreshed_at=None,
        last_error=None,
        declared_models=None,
    )

    with patch.object(agent_sources_router, "AgentSourceService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.create_model_source.return_value = payload
        mock_service_cls.return_value = mock_svc

        response = client.post(
            "/agent/sources",
            json={
                "type": "openai_compatible_gateway",
                "display_name": "Gateway source",
                "base_url": "https://gateway.example/v1",
            },
        )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()["display_name"] == "Gateway source"
    mock_svc.create_model_source.assert_awaited_once()


@pytest.mark.anyio
async def test_update_source_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    source_id = uuid.uuid4()

    with patch.object(agent_sources_router, "AgentSourceService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.update_model_source.side_effect = TracecatNotFoundError(
            f"Source {source_id} not found"
        )
        mock_service_cls.return_value = mock_svc

        response = client.patch(
            f"/agent/sources/{source_id}",
            json={"display_name": "Updated source"},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {"detail": f"Source {source_id} not found"}


@pytest.mark.anyio
async def test_refresh_source_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    source_id = uuid.uuid4()
    payload = [
        {
            "model_provider": "openai_compatible_gateway",
            "model_name": "qwen2.5:7b",
            "source_type": "openai_compatible_gateway",
            "source_name": "Gateway source",
            "source_id": source_id,
            "base_url": "https://gateway.example/v1",
            "enabled": False,
            "last_refreshed_at": None,
            "metadata": {"model_id": "qwen2.5:7b"},
            "enabled_config": None,
        }
    ]

    with patch.object(agent_sources_router, "AgentSourceService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.refresh_model_source.return_value = payload
        mock_service_cls.return_value = mock_svc

        response = client.post(f"/agent/sources/{source_id}/refresh")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()[0]["model_name"] == "qwen2.5:7b"
    mock_svc.refresh_model_source.assert_awaited_once_with(source_id=source_id)


@pytest.mark.anyio
async def test_delete_source_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    source_id = uuid.uuid4()

    with patch.object(agent_sources_router, "AgentSourceService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_service_cls.return_value = mock_svc

        response = client.delete(f"/agent/sources/{source_id}")

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert response.content == b""
    mock_svc.delete_model_source.assert_awaited_once_with(source_id=source_id)
