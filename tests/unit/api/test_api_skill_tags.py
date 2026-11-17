"""HTTP-level tests for agent preset and skill tag routes."""

import uuid
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.agent.skill.tags import definitions_router as skill_tag_definitions_router
from tracecat.agent.skill.tags import router as skill_tags_router
from tracecat.agent.tags import definitions_router as agent_tag_definitions_router
from tracecat.agent.tags import router as agent_tags_router
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams

_RESOURCE_TAG_SURFACES = [
    pytest.param(
        agent_tags_router,
        "AgentTagsService",
        "/agent/presets",
        "list_tags_for_preset_paginated",
        "Agent preset not found",
        id="agent-preset",
    ),
    pytest.param(
        skill_tags_router,
        "SkillTagsService",
        "/agent/skills",
        "list_tags_for_skill_paginated",
        "Skill not found",
        id="skill",
    ),
]
_RESOURCE_TAG_ARGS = (
    "router",
    "service_name",
    "resource_prefix",
    "list_method",
    "not_found_message",
)


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
@pytest.mark.parametrize(_RESOURCE_TAG_ARGS, _RESOURCE_TAG_SURFACES)
async def test_list_resource_tags_missing_resource_returns_404(
    client: TestClient,
    test_admin_role: Role,
    router: ModuleType,
    service_name: str,
    resource_prefix: str,
    list_method: str,
    not_found_message: str,
) -> None:
    """Missing resources should not be reported as empty tag lists."""
    with patch.object(router, service_name) as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            list_method,
            side_effect=TracecatNotFoundError(not_found_message),
        )
        mock_service_cls.return_value = mock_service

        response = client.get(f"{resource_prefix}/{uuid.uuid4()}/tags")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == not_found_message


@pytest.mark.anyio
@pytest.mark.parametrize(_RESOURCE_TAG_ARGS, _RESOURCE_TAG_SURFACES)
async def test_list_resource_tags_returns_paginated_response(
    client: TestClient,
    test_admin_role: Role,
    router: ModuleType,
    service_name: str,
    resource_prefix: str,
    list_method: str,
    not_found_message: str,
) -> None:
    """Resource tag listing should use the cursor-paginated API shape."""
    resource_id = uuid.uuid4()

    with patch.object(router, service_name) as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            list_method,
            return_value=CursorPaginatedResponse(items=[], has_more=True),
        )
        mock_service_cls.return_value = mock_service

        response = client.get(
            f"{resource_prefix}/{resource_id}/tags",
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
    getattr(mock_service, list_method).assert_awaited_once_with(
        resource_id,
        CursorPaginationParams(
            limit=2,
            cursor="encoded-cursor",
            reverse=True,
        ),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("router", "service_name", "prefix", "message"),
    [
        pytest.param(
            agent_tag_definitions_router,
            "AgentTagsService",
            "/agent-tags",
            "Agent tag already exists",
            id="agent",
        ),
        pytest.param(
            skill_tag_definitions_router,
            "SkillTagsService",
            "/skill-tags",
            "Skill tag already exists",
            id="skill",
        ),
    ],
)
async def test_update_tag_definition_conflict_returns_409(
    client: TestClient,
    test_admin_role: Role,
    router: ModuleType,
    service_name: str,
    prefix: str,
    message: str,
) -> None:
    """Tag definition conflicts should be reported as 409s."""
    with patch.object(router, service_name) as mock_service_cls:
        mock_service = MagicMock()
        mock_service.get_tag = AsyncMock(return_value=object())
        mock_service.update_tag = AsyncMock(side_effect=TracecatConflictError(message))
        mock_service_cls.return_value = mock_service

        response = client.patch(f"{prefix}/{uuid.uuid4()}", json={"name": "Updated"})

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == message
