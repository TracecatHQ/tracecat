"""HTTP-level tests for agent preset tag entitlement gating."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.agent.tags import definitions_router as agent_tag_definitions_router
from tracecat.agent.tags import router as agent_tags_router
from tracecat.auth.types import Role
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
)
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams

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
async def test_list_preset_tags_missing_preset_returns_404(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Missing presets should not be reported as empty tag lists."""
    preset_id = uuid.uuid4()

    with patch.object(agent_tags_router, "AgentTagsService") as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            "list_tags_for_preset_paginated",
            side_effect=TracecatNotFoundError("Agent preset not found"),
        )
        mock_service_cls.return_value = mock_service

        response = client.get(f"/agent/presets/{preset_id}/tags")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Agent preset not found"


@pytest.mark.anyio
async def test_list_preset_tags_returns_paginated_response(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Preset tag listing should use the cursor-paginated API shape."""
    preset_id = uuid.uuid4()

    with patch.object(agent_tags_router, "AgentTagsService") as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            "list_tags_for_preset_paginated",
            return_value=CursorPaginatedResponse(items=[], has_more=True),
        )
        mock_service_cls.return_value = mock_service

        response = client.get(
            f"/agent/presets/{preset_id}/tags",
            params={
                "limit": 2,
                "cursor": "encoded-cursor",
                "reverse": True,
            },
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "items": [],
        "next_cursor": None,
        "prev_cursor": None,
        "has_more": True,
        "has_previous": False,
        "total_estimate": None,
    }
    mock_service.list_tags_for_preset_paginated.assert_awaited_once_with(
        preset_id,
        CursorPaginationParams(
            limit=2,
            cursor="encoded-cursor",
            reverse=True,
        ),
    )


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
