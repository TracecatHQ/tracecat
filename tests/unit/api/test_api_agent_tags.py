"""HTTP-level tests for agent preset tag entitlement gating."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.agent.tags import router as agent_tags_router
from tracecat.auth.types import Role
from tracecat.exceptions import EntitlementRequired


@pytest.mark.anyio
async def test_list_preset_tags_requires_agent_addons_entitlement(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Preset tag reads should surface AGENT_ADDONS entitlement failures as 403s."""
    preset_id = uuid.uuid4()

    with patch.object(agent_tags_router, "AgentTagsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.list_tags_for_preset.side_effect = EntitlementRequired(
            "agent_addons"
        )
        mock_service_cls.return_value = mock_service

        response = client.get(f"/agent/presets/{preset_id}/tags")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["type"] == "EntitlementRequired"
    assert payload["detail"]["entitlement"] == "agent_addons"


@pytest.mark.anyio
async def test_add_preset_tag_requires_agent_addons_entitlement(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Preset tag writes should surface AGENT_ADDONS entitlement failures as 403s."""
    preset_id = uuid.uuid4()
    tag_id = uuid.uuid4()

    with patch.object(agent_tags_router, "AgentTagsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.add_preset_tag.side_effect = EntitlementRequired("agent_addons")
        mock_service_cls.return_value = mock_service

        response = client.post(
            f"/agent/presets/{preset_id}/tags",
            json={"tag_id": str(tag_id)},
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["type"] == "EntitlementRequired"
    assert payload["detail"]["entitlement"] == "agent_addons"


@pytest.mark.anyio
async def test_remove_preset_tag_requires_agent_addons_entitlement(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Preset tag deletes should surface AGENT_ADDONS entitlement failures as 403s."""
    preset_id = uuid.uuid4()
    tag_id = uuid.uuid4()

    with patch.object(agent_tags_router, "AgentTagsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_preset_tag.side_effect = EntitlementRequired("agent_addons")
        mock_service_cls.return_value = mock_service

        response = client.delete(f"/agent/presets/{preset_id}/tags/{tag_id}")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["type"] == "EntitlementRequired"
    assert payload["detail"]["entitlement"] == "agent_addons"
