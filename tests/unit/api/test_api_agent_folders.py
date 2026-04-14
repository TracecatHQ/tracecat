"""HTTP-level tests for agent folder API endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.agent.folders import router as agent_folder_router
from tracecat.agent.folders.service import (
    AGENT_FOLDER_CONFLICT_CODE,
    AGENT_FOLDER_INVALID_CODE,
    AGENT_FOLDER_PARENT_NOT_FOUND_CODE,
)
from tracecat.agent.preset import router as agent_preset_router
from tracecat.agent.preset.schemas import AgentPresetMoveToFolder
from tracecat.auth.types import Role
from tracecat.exceptions import EntitlementRequired, TracecatValidationError


@pytest.mark.anyio
async def test_create_folder_conflict_returns_409(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Duplicate folder paths should surface as conflicts."""
    with patch.object(agent_folder_router, "AgentFolderService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.create_folder.side_effect = TracecatValidationError(
            "Folder /agents/ already exists",
            detail={"code": AGENT_FOLDER_CONFLICT_CODE},
        )
        mock_service_cls.return_value = mock_service

        response = client.post(
            "/agent-folders",
            json={"name": "agents", "parent_path": "/"},
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["detail"] == "Folder /agents/ already exists"


@pytest.mark.anyio
async def test_create_folder_missing_parent_returns_404(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Missing parent paths should not be misreported as conflicts."""
    with patch.object(agent_folder_router, "AgentFolderService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.create_folder.side_effect = TracecatValidationError(
            "Parent path /missing/ not found",
            detail={"code": AGENT_FOLDER_PARENT_NOT_FOUND_CODE},
        )
        mock_service_cls.return_value = mock_service

        response = client.post(
            "/agent-folders",
            json={"name": "child", "parent_path": "/missing/"},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["detail"] == "Parent path /missing/ not found"


@pytest.mark.anyio
async def test_update_folder_validation_returns_400(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Invalid rename requests should stay in the 4xx range."""
    with patch.object(agent_folder_router, "AgentFolderService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.rename_folder.side_effect = TracecatValidationError(
            "Folder name cannot contain slashes",
            detail={"code": AGENT_FOLDER_INVALID_CODE},
        )
        mock_service_cls.return_value = mock_service

        response = client.patch(
            f"/agent-folders/{uuid.uuid4()}",
            json={"name": "bad/name"},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["detail"] == "Folder name cannot contain slashes"


@pytest.mark.anyio
async def test_move_folder_validation_returns_400(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Cyclic folder moves should not fall through as 500s."""
    with patch.object(agent_folder_router, "AgentFolderService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.move_folder.side_effect = TracecatValidationError(
            "Cannot create cyclic folder structure",
            detail={"code": AGENT_FOLDER_INVALID_CODE},
        )
        mock_service_cls.return_value = mock_service

        response = client.post(
            f"/agent-folders/{uuid.uuid4()}/move",
            json={"new_parent_path": "/"},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["detail"] == "Cannot create cyclic folder structure"


@pytest.mark.anyio
async def test_move_agent_preset_to_root_skips_folder_lookup(
    test_admin_role: Role,
) -> None:
    """Moving to '/' should clear the folder instead of trying to resolve a root row."""
    preset_id = uuid.uuid4()
    session = AsyncMock()

    with patch.object(agent_preset_router, "AgentFolderService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.move_preset.return_value = None
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
async def test_move_agent_preset_requires_agent_addons_entitlement(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Preset moves should surface AGENT_ADDONS entitlement failures as 403s."""
    preset_id = uuid.uuid4()

    with patch.object(agent_preset_router, "AgentFolderService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.move_preset.side_effect = EntitlementRequired("agent_addons")
        mock_service_cls.return_value = mock_service

        response = client.post(
            f"/agent/presets/{preset_id}/move",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"folder_path": "/"},
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["type"] == "EntitlementRequired"
    assert payload["detail"]["entitlement"] == "agent_addons"


@pytest.mark.anyio
async def test_get_directory_requires_agent_addons_entitlement(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Folder directory reads should preserve the AGENT_ADDONS gate at HTTP level."""
    with patch.object(agent_folder_router, "AgentFolderService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_directory_items.side_effect = EntitlementRequired(
            "agent_addons"
        )
        mock_service_cls.return_value = mock_service

        response = client.get("/agent-folders/directory", params={"path": "/"})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["type"] == "EntitlementRequired"
    assert payload["detail"]["entitlement"] == "agent_addons"
