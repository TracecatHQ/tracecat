"""HTTP-level tests for agent and skill folder API endpoints."""

import uuid
from enum import StrEnum
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.agent.folders import router as agent_folder_router
from tracecat.agent.folders.service import AgentFolderErrorCode
from tracecat.agent.preset import router as agent_preset_router
from tracecat.agent.preset.schemas import AgentPresetMoveToFolder
from tracecat.agent.skill import router as skill_router
from tracecat.agent.skill.folders import router as skill_folder_router
from tracecat.agent.skill.folders.service import SkillFolderErrorCode
from tracecat.agent.skill.schemas import SkillMoveToFolder
from tracecat.auth.types import Role
from tracecat.exceptions import TracecatValidationError
from tracecat.pagination import CursorPaginatedResponse, CursorPaginationParams

_FOLDER_SURFACES = [
    pytest.param(
        agent_folder_router,
        "AgentFolderService",
        "/agent-folders",
        AgentFolderErrorCode,
        id="agent",
    ),
    pytest.param(
        skill_folder_router,
        "SkillFolderService",
        "/skill-folders",
        SkillFolderErrorCode,
        id="skill",
    ),
]
_SURFACE_ARGS = ("router", "service_name", "prefix", "error_codes")


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
@pytest.mark.parametrize(_SURFACE_ARGS, _FOLDER_SURFACES)
@pytest.mark.parametrize(
    ("method", "suffix", "kwargs", "service_method", "message", "code", "status_code"),
    [
        pytest.param(
            "POST",
            "",
            {"json": {"name": "agents", "parent_path": "/"}},
            "create_folder",
            "Folder /agents/ already exists",
            "CONFLICT",
            status.HTTP_409_CONFLICT,
            id="create-conflict",
        ),
        pytest.param(
            "POST",
            "",
            {"json": {"name": "child", "parent_path": "/missing/"}},
            "create_folder",
            "Parent path /missing/ not found",
            "PARENT_NOT_FOUND",
            status.HTTP_404_NOT_FOUND,
            id="create-missing-parent",
        ),
        pytest.param(
            "GET",
            "",
            {"params": {"parent_path": "/missing/"}},
            "list_folders_paginated",
            "Parent path /missing/ not found",
            "PARENT_NOT_FOUND",
            status.HTTP_404_NOT_FOUND,
            id="list-missing-parent",
        ),
        pytest.param(
            "POST",
            "",
            {"json": {"name": "   ", "parent_path": "/"}},
            "create_folder",
            "Folder name cannot be empty",
            "INVALID",
            status.HTTP_400_BAD_REQUEST,
            id="create-blank-name",
        ),
        pytest.param(
            "PATCH",
            f"/{uuid.uuid4()}",
            {"json": {"name": "bad/name"}},
            "rename_folder",
            "Folder name cannot contain slashes",
            "INVALID",
            status.HTTP_400_BAD_REQUEST,
            id="update-invalid-name",
        ),
        pytest.param(
            "PATCH",
            f"/{uuid.uuid4()}",
            {"json": {"name": "   "}},
            "rename_folder",
            "Folder name cannot be empty",
            "INVALID",
            status.HTTP_400_BAD_REQUEST,
            id="update-blank-name",
        ),
        pytest.param(
            "POST",
            f"/{uuid.uuid4()}/move",
            {"json": {"new_parent_path": "/"}},
            "move_folder",
            "Cannot create cyclic folder structure",
            "INVALID",
            status.HTTP_400_BAD_REQUEST,
            id="move-cyclic",
        ),
    ],
)
async def test_folder_validation_errors_map_to_http_status(
    client: TestClient,
    test_admin_role: Role,
    router: ModuleType,
    service_name: str,
    prefix: str,
    error_codes: type[StrEnum],
    method: str,
    suffix: str,
    kwargs: dict[str, dict[str, str]],
    service_method: str,
    message: str,
    code: str,
    status_code: int,
) -> None:
    """Folder validation failures should map to the matching 4xx status."""
    with patch.object(router, service_name) as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            service_method,
            side_effect=TracecatValidationError(
                message, detail={"code": error_codes[code].value}
            ),
        )
        mock_service_cls.return_value = mock_service

        response = client.request(method, prefix + suffix, **kwargs)

    assert response.status_code == status_code
    assert response.json()["detail"] == message


@pytest.mark.anyio
@pytest.mark.parametrize(_SURFACE_ARGS, _FOLDER_SURFACES)
async def test_list_folders_returns_paginated_response(
    client: TestClient,
    test_admin_role: Role,
    router: ModuleType,
    service_name: str,
    prefix: str,
    error_codes: type[StrEnum],
) -> None:
    """Folder listing should use the cursor-paginated API shape."""
    with patch.object(router, service_name) as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            "list_folders_paginated",
            return_value=CursorPaginatedResponse(items=[], has_more=True),
        )
        mock_service_cls.return_value = mock_service

        response = client.get(
            prefix,
            params={
                "parent_path": "/agents/",
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
    mock_service.list_folders_paginated.assert_awaited_once_with(
        parent_path="/agents/",
        params=CursorPaginationParams(
            limit=2,
            cursor="encoded-cursor",
            reverse=True,
        ),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(_SURFACE_ARGS, _FOLDER_SURFACES)
async def test_get_directory_returns_directory_items(
    client: TestClient,
    test_admin_role: Role,
    router: ModuleType,
    service_name: str,
    prefix: str,
    error_codes: type[StrEnum],
) -> None:
    """Directory listing should return the full directory array."""
    with patch.object(router, service_name) as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            "get_directory_items",
            return_value=[],
        )
        mock_service_cls.return_value = mock_service

        response = client.get(f"{prefix}/directory", params={"path": "/agents/"})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []
    mock_service.get_directory_items.assert_awaited_once_with(
        "/agents/", order_by="desc"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(_SURFACE_ARGS, _FOLDER_SURFACES)
async def test_delete_folder_without_body_defaults_to_non_recursive(
    client: TestClient,
    test_admin_role: Role,
    router: ModuleType,
    service_name: str,
    prefix: str,
    error_codes: type[StrEnum],
) -> None:
    """Non-recursive folder deletes should not require a request body."""
    folder_id = uuid.uuid4()

    with patch.object(router, service_name) as mock_service_cls:
        mock_service = _mock_service_with_async_method("delete_folder")
        mock_service_cls.return_value = mock_service

        response = client.delete(f"{prefix}/{folder_id}")

    assert response.status_code == status.HTTP_204_NO_CONTENT
    mock_service.delete_folder.assert_awaited_once_with(folder_id, recursive=False)


@pytest.mark.anyio
async def test_move_agent_preset_to_root_skips_folder_lookup(
    test_admin_role: Role,
) -> None:
    """Moving to '/' should clear the folder instead of trying to resolve a root row."""
    preset_id = uuid.uuid4()
    session = AsyncMock()

    with patch.object(agent_preset_router, "AgentFolderService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service.move_preset = AsyncMock(return_value=None)
        mock_service.get_folder_by_path = AsyncMock()
        mock_service_cls.return_value = mock_service

        await agent_preset_router.move_agent_preset_to_folder(
            role=test_admin_role,
            session=session,
            preset_id=preset_id,
            params=AgentPresetMoveToFolder(folder_path="/"),
        )

        mock_service.get_folder_by_path.assert_not_called()
        mock_service.move_preset.assert_awaited_once_with(preset_id, None)


@pytest.mark.anyio
async def test_move_skill_to_root_skips_folder_lookup(
    test_admin_role: Role,
) -> None:
    """Moving to '/' should clear the folder instead of trying to resolve a root row."""
    skill_id = uuid.uuid4()
    session = AsyncMock()

    with patch.object(skill_router, "SkillFolderService") as mock_service_cls:
        mock_service = MagicMock()
        mock_service.move_skill = AsyncMock(return_value=None)
        mock_service.get_folder_by_path = AsyncMock()
        mock_service_cls.return_value = mock_service

        await skill_router.move_skill(
            role=test_admin_role,
            session=session,
            skill_id=skill_id,
            params=SkillMoveToFolder(folder_path="/"),
        )

        mock_service.get_folder_by_path.assert_not_called()
        mock_service.move_skill.assert_awaited_once_with(skill_id, None)
