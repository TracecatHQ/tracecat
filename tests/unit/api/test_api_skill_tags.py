"""HTTP-level tests for skill tag entitlement gating."""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.agent.skill.tags import definitions_router as skill_tag_definitions_router
from tracecat.agent.skill.tags import router as skill_tags_router
from tracecat.auth.types import Role
from tracecat.exceptions import (
    EntitlementRequired,
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
async def test_list_skill_tags_requires_agent_addons_entitlement(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Skill tag reads should surface AGENT_ADDONS entitlement failures as 403s."""
    skill_id = uuid.uuid4()

    with patch.object(skill_tags_router, "SkillTagsService") as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            "list_tags_for_skill_paginated",
            side_effect=EntitlementRequired("agent_addons"),
        )
        mock_service_cls.return_value = mock_service

        response = client.get(f"/agent/skills/{skill_id}/tags")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["type"] == "EntitlementRequired"
    assert payload["detail"]["entitlement"] == "agent_addons"


@pytest.mark.anyio
async def test_list_skill_tags_missing_skill_returns_404(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Missing skills should not be reported as empty tag lists."""
    skill_id = uuid.uuid4()

    with patch.object(skill_tags_router, "SkillTagsService") as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            "list_tags_for_skill_paginated",
            side_effect=TracecatNotFoundError("Skill not found"),
        )
        mock_service_cls.return_value = mock_service

        response = client.get(f"/agent/skills/{skill_id}/tags")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Skill not found"


@pytest.mark.anyio
async def test_list_skill_tag_definitions_returns_paginated_response(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Skill tag listing should use the cursor-paginated API shape."""
    skill_id = uuid.uuid4()

    with patch.object(skill_tags_router, "SkillTagsService") as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            "list_tags_for_skill_paginated",
            return_value=CursorPaginatedResponse(items=[], has_more=True),
        )
        mock_service_cls.return_value = mock_service

        response = client.get(
            f"/agent/skills/{skill_id}/tags",
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
    mock_service.list_tags_for_skill_paginated.assert_awaited_once_with(
        skill_id,
        CursorPaginationParams(
            limit=2,
            cursor="encoded-cursor",
            reverse=True,
        ),
    )


@pytest.mark.anyio
async def test_add_skill_tag_requires_agent_addons_entitlement(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Skill tag writes should surface AGENT_ADDONS entitlement failures as 403s."""
    skill_id = uuid.uuid4()
    tag_id = uuid.uuid4()

    with patch.object(skill_tags_router, "SkillTagsService") as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            "add_skill_tag",
            side_effect=EntitlementRequired("agent_addons"),
        )
        mock_service_cls.return_value = mock_service

        response = client.post(
            f"/agent/skills/{skill_id}/tags",
            json={"tag_id": str(tag_id)},
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["type"] == "EntitlementRequired"
    assert payload["detail"]["entitlement"] == "agent_addons"


@pytest.mark.anyio
async def test_add_skill_tag_success_returns_201(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Skill tag writes should return 201 when the service accepts the request."""
    skill_id = uuid.uuid4()
    tag_id = uuid.uuid4()

    with patch.object(skill_tags_router, "SkillTagsService") as mock_service_cls:
        mock_service = _mock_service_with_async_method("add_skill_tag")
        mock_service_cls.return_value = mock_service

        response = client.post(
            f"/agent/skills/{skill_id}/tags",
            json={"tag_id": str(tag_id)},
        )

    assert response.status_code == status.HTTP_201_CREATED


@pytest.mark.anyio
async def test_remove_skill_tag_requires_agent_addons_entitlement(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Skill tag deletes should surface AGENT_ADDONS entitlement failures as 403s."""
    skill_id = uuid.uuid4()
    tag_id = uuid.uuid4()

    with patch.object(skill_tags_router, "SkillTagsService") as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            "get_skill_tag",
            side_effect=EntitlementRequired("agent_addons"),
        )
        mock_service_cls.return_value = mock_service

        response = client.delete(f"/agent/skills/{skill_id}/tags/{tag_id}")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    payload = response.json()
    assert payload["type"] == "EntitlementRequired"
    assert payload["detail"]["entitlement"] == "agent_addons"


@pytest.mark.anyio
async def test_list_skill_tags_returns_paginated_response(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Skill tag listing should use the cursor-paginated API shape."""
    with patch.object(
        skill_tag_definitions_router, "SkillTagsService"
    ) as mock_service_cls:
        mock_service = _mock_service_with_async_method(
            "list_tags_paginated",
            return_value=CursorPaginatedResponse(items=[]),
        )
        mock_service_cls.return_value = mock_service

        response = client.get("/skill-tags")

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
async def test_update_skill_tag_conflict_returns_409(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Skill tag definition conflicts should be reported as 409s."""
    tag_id = uuid.uuid4()

    with patch.object(
        skill_tag_definitions_router, "SkillTagsService"
    ) as mock_service_cls:
        mock_service = MagicMock()
        mock_service.get_tag = AsyncMock(return_value=object())
        mock_service.update_tag = AsyncMock(
            side_effect=TracecatConflictError("Skill tag already exists")
        )
        mock_service_cls.return_value = mock_service

        response = client.patch(
            f"/skill-tags/{tag_id}",
            json={"name": "Updated"},
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == "Skill tag already exists"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "kwargs", "service_method"),
    [
        ("get", "/skill-tags", {}, "list_tags_paginated"),
        ("get", f"/skill-tags/{_FIXED_TAG_ID}", {}, "get_tag"),
        (
            "post",
            "/skill-tags",
            {"json": {"name": "Urgent", "color": "#000000"}},
            "create_tag",
        ),
        (
            "patch",
            f"/skill-tags/{_FIXED_TAG_ID}",
            {"json": {"name": "Updated"}},
            "get_tag",
        ),
        ("delete", f"/skill-tags/{_FIXED_TAG_ID}", {}, "delete_tag_by_id"),
    ],
)
async def test_skill_tag_definition_routes_require_agent_addons_entitlement(
    client: TestClient,
    test_admin_role: Role,
    method: str,
    path: str,
    kwargs: dict[str, Any],
    service_method: str,
) -> None:
    """Skill tag definition CRUD should surface AGENT_ADDONS failures as 403s."""
    with patch.object(
        skill_tag_definitions_router, "SkillTagsService"
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
