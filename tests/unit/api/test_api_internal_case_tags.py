import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tracecat.auth.types import Role
from tracecat.cases.tags import internal_router as internal_case_tags_router
from tracecat.tags.schemas import TagCreate


@pytest.mark.anyio
async def test_internal_add_case_tag_invalid_tag_name_returns_400(
    action_gateway_client: TestClient,
    test_admin_role: Role,
) -> None:
    """An auto-created tag name that fails TagCreate validation is a client error."""
    case_id = uuid.uuid4()
    tag_name = "t" * 51
    with pytest.raises(ValidationError) as exc_info:
        TagCreate(name=tag_name)
    with patch.object(internal_case_tags_router, "CaseTagsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.add_case_tag.side_effect = exc_info.value
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.post(
            f"/internal/cases/{case_id}/tags",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"tag_id": tag_name, "create_if_missing": True},
        )

    mock_service.add_case_tag.assert_awaited_once_with(
        case_id, tag_name, create_if_missing=True
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "string_too_long" in response.json()["detail"]
