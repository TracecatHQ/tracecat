"""HTTP-level tests for agent preset tag entitlement gating."""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.agent.tags import definitions_router as agent_tag_definitions_router
from tracecat.agent.tags import router as agent_tags_router
from tracecat.auth.types import Role
from tracecat.exceptions import (
    EntitlementRequired,
    TracecatConflictError,
    TracecatNotFoundError,
)
from tracecat.pagination import CursorPaginatedResponse

# Fixed UUID for parametrized test IDs — uuid.uuid4() at module level causes
# pytest-xdist collection mismatches because each worker generates a different value.
_FIXED_TAG_ID = "00000000-0000-4000-8000-000000000002"


def _mock_service_with_async_method(
    method_name: str,
    *,
    side_effect: Exception | None = None,
    return_value: object = None,
) -> MagicMock:
    service = MagicMock()
    service_method = AsyncMock()
    if side_effect is not None:
        service_method.side_effect = side_effect
    else:
        service_method.return_value = return_value
    setattr(service, method_name, service_method)
    return service


@pytest.mark.anyio
async def test_list_preset_tags_requires_agent_addons_entitlement(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Preset tag reads should surface AGENT_ADDONS entitlement failures as 403s."""
    preset_id = uuid.uuid4()

    with patch.object(agent_tags_router, "AgentTagsService") as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            "list_tags_for_preset",
            side_effect=EntitlementRequired("agent_addons"),
        )
        mock_service_cls.return_value = mock_service

        response = client.get(f"/agent/presets/{preset_id}/tags")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["type"] == "EntitlementRequired"
    assert payload["detail"]["entitlement"] == "agent_addons"


@pytest.mark.anyio
async def test_list_preset_tags_missing_preset_returns_404(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Missing presets should not be reported as empty tag lists."""
    preset_id = uuid.uuid4()

    with patch.object(agent_tags_router, "AgentTagsService") as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            "list_tags_for_preset",
            side_effect=TracecatNotFoundError("Agent preset not found"),
        )
        mock_service_cls.return_value = mock_service

        response = client.get(f"/agent/presets/{preset_id}/tags")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Agent preset not found"


@pytest.mark.anyio
async def test_add_preset_tag_requires_agent_addons_entitlement(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Preset tag writes should surface AGENT_ADDONS entitlement failures as 403s."""
    preset_id = uuid.uuid4()
    tag_id = uuid.uuid4()

    with patch.object(agent_tags_router, "AgentTagsService") as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            "add_preset_tag",
            side_effect=EntitlementRequired("agent_addons"),
        )
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
async def test_add_preset_tag_success_returns_201(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Preset tag writes should return 201 when the service accepts the request."""
    preset_id = uuid.uuid4()
    tag_id = uuid.uuid4()

    with patch.object(agent_tags_router, "AgentTagsService") as mock_service_cls:
        mock_service = _mock_service_with_async_method("add_preset_tag")
        mock_service_cls.return_value = mock_service

        response = client.post(
            f"/agent/presets/{preset_id}/tags",
            json={"tag_id": str(tag_id)},
        )

    assert response.status_code == status.HTTP_201_CREATED


@pytest.mark.anyio
async def test_remove_preset_tag_requires_agent_addons_entitlement(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Preset tag deletes should surface AGENT_ADDONS entitlement failures as 403s."""
    preset_id = uuid.uuid4()
    tag_id = uuid.uuid4()

    with patch.object(agent_tags_router, "AgentTagsService") as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            "get_preset_tag",
            side_effect=EntitlementRequired("agent_addons"),
        )
        mock_service_cls.return_value = mock_service

        response = client.delete(f"/agent/presets/{preset_id}/tags/{tag_id}")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["type"] == "EntitlementRequired"
    assert payload["detail"]["entitlement"] == "agent_addons"


@pytest.mark.anyio
async def test_list_agent_tags_returns_paginated_response(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Agent tag listing should use the cursor-paginated API shape."""
    with patch.object(
        agent_tag_definitions_router, "AgentTagsService"
    ) as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            "list_tags_paginated",
            return_value=CursorPaginatedResponse(items=[]),
        )
        mock_service_cls.return_value = mock_service

        response = client.get("/agent-tags")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "items": [],
        "next_cursor": None,
        "prev_cursor": None,
        "has_more": False,
        "has_previous": False,
        "total_estimate": None,
    }


@pytest.mark.anyio
async def test_update_agent_tag_conflict_returns_409(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Agent tag definition conflicts should be reported as 409s."""
    tag_id = uuid.uuid4()

    with patch.object(
        agent_tag_definitions_router, "AgentTagsService"
    ) as mock_service_cls:
        mock_service = MagicMock()
        mock_service.get_tag = AsyncMock(return_value=object())
        mock_service.update_tag = AsyncMock(
            side_effect=TracecatConflictError("Agent tag already exists")
        )
        mock_service_cls.return_value = mock_service

        response = client.patch(
            f"/agent-tags/{tag_id}",
            json={"name": "Updated"},
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == "Agent tag already exists"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "kwargs", "service_method"),
    [
        ("get", "/agent-tags", {}, "list_tags_paginated"),
        ("get", f"/agent-tags/{_FIXED_TAG_ID}", {}, "get_tag"),
        (
            "post",
            "/agent-tags",
            {"json": {"name": "Urgent", "color": "#000000"}},
            "create_tag",
        ),
        (
            "patch",
            f"/agent-tags/{_FIXED_TAG_ID}",
            {"json": {"name": "Updated"}},
            "get_tag",
        ),
        ("delete", f"/agent-tags/{_FIXED_TAG_ID}", {}, "delete_tag_by_id"),
    ],
)
async def test_agent_tag_definition_routes_require_agent_addons_entitlement(
    client: TestClient,
    test_admin_role: Role,
    method: str,
    path: str,
    kwargs: dict[str, Any],
    service_method: str,
) -> None:
    """Agent tag definition CRUD should surface AGENT_ADDONS failures as 403s."""
    with patch.object(
        agent_tag_definitions_router, "AgentTagsService"
    ) as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            service_method,
            side_effect=EntitlementRequired("agent_addons"),
        )
        mock_service_cls.return_value = mock_service

        response = getattr(client, method)(path, **kwargs)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["type"] == "EntitlementRequired"
    assert payload["detail"]["entitlement"] == "agent_addons"
