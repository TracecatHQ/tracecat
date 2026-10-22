"""HTTP-level tests for tag and variable validation."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import NoResultFound

from tracecat.auth.types import Role
from tracecat.exceptions import TracecatConflictError
from tracecat.tags import router as tags_router


@pytest.mark.anyio
async def test_duplicate_tag_maps_to_http_409(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(tags_router, "TagsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.create_tag.side_effect = TracecatConflictError(
            "Tag with this name already exists"
        )
        mock_service_cls.return_value = mock_service

        response = client.post(
            "/tags",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"name": "Synthetic tag"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "Tag with this name already exists"


@pytest.mark.anyio
async def test_missing_tag_update_maps_to_http_404(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(tags_router, "TagsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_tag.side_effect = NoResultFound
        mock_service_cls.return_value = mock_service

        response = client.patch(
            f"/tags/{uuid.uuid4()}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"name": "Synthetic renamed tag"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Tag not found"


@pytest.mark.anyio
async def test_invalid_variable_name_is_request_validation_error(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    response = client.get(
        "/variables/search",
        params={
            "workspace_id": str(test_admin_role.workspace_id),
            "name": "invalid-variable-name",
        },
    )

    assert response.status_code == 422
