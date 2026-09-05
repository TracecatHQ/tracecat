"""HTTP-level tests for the workspace membership removal route."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.db.models import Workspace
from tracecat.exceptions import GroupDerivedMembershipError

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.usefixtures("db", "test_admin_role"),
]


async def test_delete_membership_returns_409_for_group_derived_member(
    client: TestClient, test_workspace: Workspace
) -> None:
    """A group-derived member's removal is refused with the machine-readable code."""
    user_id = uuid.uuid4()
    service = AsyncMock()
    service.delete_membership.side_effect = GroupDerivedMembershipError(
        "group derived", group_names=["engineering", "platform"]
    )

    with patch("tracecat.workspaces.router.MembershipService", return_value=service):
        response = client.delete(
            f"/workspaces/{test_workspace.id}/memberships/{user_id}"
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == {
        "code": "group_derived_membership",
        "group_names": ["engineering", "platform"],
    }


async def test_delete_membership_returns_204_for_direct_member(
    client: TestClient, test_workspace: Workspace
) -> None:
    """A directly assigned member is removed and the route returns 204."""
    user_id = uuid.uuid4()
    service = AsyncMock()
    service.delete_membership.return_value = None

    with patch("tracecat.workspaces.router.MembershipService", return_value=service):
        response = client.delete(
            f"/workspaces/{test_workspace.id}/memberships/{user_id}"
        )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    service.delete_membership.assert_awaited_once_with(
        test_workspace.id, user_id=user_id
    )
