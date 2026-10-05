"""HTTP-level tests for organization members API endpoints."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import get_args
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session, get_async_session_bypass_rls
from tracecat.exceptions import TracecatConflictError
from tracecat.organization import router as organization_router
from tracecat.organization.schemas import (
    OrgMemberAccess,
    OrgMemberGroupRead,
    OrgMemberRoleRead,
    OrgMemberWorkspaceRead,
)


def _member_user(user_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id or uuid.uuid4(),
        email="member@example.com",
        first_name="Member",
        last_name="User",
        is_active=True,
        is_superuser=True,
        is_verified=True,
        last_login_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _override_role_dependency() -> Role:
    role = ctx_role.get()
    if role is None:
        raise RuntimeError("No role set in ctx_role context")
    return role


@pytest.fixture(autouse=True)
def _override_organization_role_dependencies(  # pyright: ignore[reportUnusedFunction]
    client: TestClient,
):
    role_dependencies = [
        organization_router.OrgUserRole,
    ]

    for annotated_type in role_dependencies:
        metadata = get_args(annotated_type)
        if metadata and hasattr(metadata[1], "dependency"):
            dependency = metadata[1].dependency
            app.dependency_overrides[dependency] = _override_role_dependency

    yield

    for annotated_type in role_dependencies:
        metadata = get_args(annotated_type)
        if metadata and hasattr(metadata[1], "dependency"):
            dependency = metadata[1].dependency
            app.dependency_overrides.pop(dependency, None)


@pytest.mark.anyio
async def test_list_current_user_organization_memberships(
    client: TestClient, test_admin_role: Role
) -> None:
    first_org_id = uuid.uuid4()
    second_org_id = uuid.uuid4()
    mock_session = await app.dependency_overrides[get_async_session_bypass_rls]()

    memberships_result = Mock()
    memberships_result.all.return_value = [
        (first_org_id, "Alpha"),
        (second_org_id, "Beta"),
    ]
    mock_session.execute = AsyncMock(return_value=memberships_result)

    response = client.get("/organization/memberships")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == [
        {"id": str(first_org_id), "name": "Alpha"},
        {"id": str(second_org_id), "name": "Beta"},
    ]

    # Tenant-isolation guard: the query must filter memberships by the
    # authenticated user's id and only return active organizations. Compile
    # the actual statement passed to execute so a regression that drops or
    # broadens the user_id predicate fails here.
    execute_await_args = mock_session.execute.await_args
    assert execute_await_args is not None
    stmt = execute_await_args.args[0]
    compiled = stmt.compile()
    sql = str(compiled)

    # Org presence is stored; the filter lands on the membership table.
    assert "organization_membership.user_id = " in sql
    assert "organization.is_active" in sql
    assert test_admin_role.user_id in compiled.params.values()


@pytest.mark.anyio
async def test_list_org_members_omits_superuser_flag(
    client: TestClient, test_admin_role: Role
) -> None:
    user = _member_user()
    mock_session = await app.dependency_overrides[get_async_session]()
    mock_session.execute = AsyncMock()

    with (
        patch.object(organization_router, "OrgService") as MockService,
        patch.object(organization_router, "InvitationService") as MockInvitationService,
    ):
        mock_svc = AsyncMock()
        mock_svc.list_members.return_value = [user]
        mock_svc.list_member_access.return_value = {
            user.id: OrgMemberAccess(
                role_name="Organization Admin", role_slug="organization-admin"
            )
        }
        mock_svc.list_invitation_access.return_value = {}
        MockService.return_value = mock_svc
        MockInvitationService.return_value.list_invitations = AsyncMock(return_value=[])

        response = client.get("/organization/members")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["user_id"] == str(user.id)
    assert "is_superuser" not in data[0]
    mock_session.execute.assert_not_awaited()


@pytest.mark.anyio
async def test_list_org_members_labels_roleless_member(
    client: TestClient, test_admin_role: Role
) -> None:
    user = _member_user()
    mock_session = await app.dependency_overrides[get_async_session]()
    mock_session.execute = AsyncMock()

    with (
        patch.object(organization_router, "OrgService") as MockService,
        patch.object(organization_router, "InvitationService") as MockInvitationService,
    ):
        mock_svc = AsyncMock()
        mock_svc.list_members.return_value = [user]
        mock_svc.list_member_access.return_value = {user.id: OrgMemberAccess()}
        mock_svc.list_invitation_access.return_value = {}
        MockService.return_value = mock_svc
        MockInvitationService.return_value.list_invitations = AsyncMock(return_value=[])

        response = client.get("/organization/members")

    assert response.status_code == status.HTTP_200_OK
    [member] = response.json()
    assert member["role_name"] == "Member"
    assert member["role_slug"] is None
    mock_session.execute.assert_not_awaited()


@pytest.mark.anyio
async def test_list_org_members_serializes_member_and_invitation_access(
    client: TestClient, test_admin_role: Role
) -> None:
    user = _member_user()
    role_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    group_id = uuid.uuid4()
    invitation_id = uuid.uuid4()
    invitation_role_id = uuid.uuid4()
    invitation_workspace_id = uuid.uuid4()
    now = datetime.now(UTC)
    mock_session = await app.dependency_overrides[get_async_session]()
    mock_session.execute = AsyncMock()
    invitation = SimpleNamespace(
        id=invitation_id,
        email="invitee@example.com",
        expires_at=now.replace(year=now.year + 1),
        created_at=now,
        grants=[
            SimpleNamespace(workspace_id=None, role_id=invitation_role_id),
            SimpleNamespace(
                workspace_id=invitation_workspace_id, role_id=invitation_role_id
            ),
        ],
    )

    member_access = OrgMemberAccess(
        role_name="Organization Admin",
        role_slug="organization-admin",
        roles=[OrgMemberRoleRead(id=role_id, name="Incident Responder")],
        workspaces=[
            OrgMemberWorkspaceRead(id=workspace_id, name="Security Operations")
        ],
        groups=[OrgMemberGroupRead(id=group_id, name="Response Team")],
    )
    invitation_access = OrgMemberAccess(
        role_name="Invited",
        role_slug="organization-admin",
        roles=[OrgMemberRoleRead(id=invitation_role_id, name="Organization Admin")],
        workspaces=[
            OrgMemberWorkspaceRead(
                id=invitation_workspace_id, name="Detection Engineering"
            )
        ],
    )
    with (
        patch.object(organization_router, "OrgService") as MockService,
        patch.object(organization_router, "InvitationService") as MockInvitationService,
    ):
        mock_svc = AsyncMock()
        mock_svc.list_members.return_value = [user]
        mock_svc.list_member_access.return_value = {user.id: member_access}
        mock_svc.list_invitation_access.return_value = {
            invitation_id: invitation_access
        }
        MockService.return_value = mock_svc
        MockInvitationService.return_value.list_invitations = AsyncMock(
            return_value=[invitation]
        )

        response = client.get("/organization/members")

    assert response.status_code == status.HTTP_200_OK
    active, invited = response.json()
    assert active["roles"] == [{"id": str(role_id), "name": "Incident Responder"}]
    assert active["workspaces"] == [
        {"id": str(workspace_id), "name": "Security Operations"}
    ]
    assert active["groups"] == [{"id": str(group_id), "name": "Response Team"}]
    assert invited["invitation_id"] == str(invitation_id)
    assert invited["role_slug"] == "organization-admin"
    assert invited["roles"] == [
        {"id": str(invitation_role_id), "name": "Organization Admin"}
    ]
    assert invited["workspaces"] == [
        {"id": str(invitation_workspace_id), "name": "Detection Engineering"}
    ]
    assert invited["groups"] == []
    mock_session.execute.assert_not_awaited()


@pytest.mark.anyio
async def test_update_org_member_omits_superuser_flag(
    client: TestClient, test_admin_role: Role
) -> None:
    user = _member_user()
    mock_session = await app.dependency_overrides[get_async_session]()

    # Mock the RBAC role name query result
    rbac_result = Mock()
    rbac_result.scalar_one_or_none.return_value = "Admin"
    mock_session.execute = AsyncMock(return_value=rbac_result)

    with patch.object(organization_router, "OrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.update_member.return_value = user
        MockService.return_value = mock_svc

        response = client.patch(
            f"/organization/members/{user.id}",
            json={"role": "basic"},
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["user_id"] == str(user.id)
    assert data["role"] == "Admin"
    assert "is_superuser" not in data


@pytest.mark.anyio
async def test_delete_org_member_scim_managed_returns_conflict(
    client: TestClient, test_admin_role: Role
) -> None:
    user = _member_user()

    with patch.object(organization_router, "OrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.delete_member.side_effect = TracecatConflictError(
            "This member is managed by your identity provider. "
            "Deprovision them there to remove their access."
        )
        MockService.return_value = mock_svc

        response = client.delete(f"/organization/members/{user.id}")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "identity provider" in response.json()["detail"]


@pytest.mark.anyio
async def test_delete_org_member_ordinary_user_succeeds(
    client: TestClient, test_admin_role: Role
) -> None:
    user = _member_user()

    with patch.object(organization_router, "OrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.delete_member.return_value = None
        MockService.return_value = mock_svc

        response = client.delete(f"/organization/members/{user.id}")

    assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.anyio
async def test_delete_organization_requires_owner_role(
    client: TestClient, test_admin_role: Role
) -> None:
    response = client.delete("/organization?confirm=Test%20Organization")
    assert response.status_code == status.HTTP_403_FORBIDDEN
