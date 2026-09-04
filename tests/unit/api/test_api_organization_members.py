"""HTTP-level tests for organization members API endpoints."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import get_args
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat import config
from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session, get_async_session_bypass_rls
from tracecat.email.client import Mailer, OutboundEmail
from tracecat.invitations.enums import InvitationStatus
from tracecat.organization import router as organization_router


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

    assert "organization_membership.user_id = " in sql
    assert "organization.is_active" in sql
    assert test_admin_role.user_id in compiled.params.values()


@pytest.mark.anyio
async def test_list_org_members_omits_superuser_flag(
    client: TestClient, test_admin_role: Role
) -> None:
    user = _member_user()
    mock_session = await app.dependency_overrides[get_async_session]()

    # Mock the RBAC role lookup query result
    rbac_tuples = Mock()
    rbac_tuples.all.return_value = [(user.id, "Admin", "organization-admin")]
    rbac_result = Mock()
    rbac_result.tuples.return_value = rbac_tuples

    # Mock the invitations query result
    inv_result = Mock()
    inv_result.scalars.return_value = Mock(all=Mock(return_value=[]))

    mock_session.execute = AsyncMock(side_effect=[rbac_result, inv_result])

    with patch.object(organization_router, "OrgService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.list_members.return_value = [user]
        mock_svc.list_invitations.return_value = []
        MockService.return_value = mock_svc

        response = client.get("/organization/members")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["user_id"] == str(user.id)
    assert "is_superuser" not in data[0]


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
async def test_create_org_invitation_schedules_configured_email(
    client: TestClient, test_admin_role: Role
) -> None:
    organization_id = test_admin_role.organization_id
    assert organization_id is not None
    role_id = uuid.uuid4()
    invitation = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        email="invitee@example.com",
        role_id=role_id,
        role_obj=SimpleNamespace(name="Member", slug="organization-member"),
        status=InvitationStatus.PENDING,
        invited_by=test_admin_role.user_id,
        expires_at=datetime(2026, 1, 8, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        accepted_at=None,
        token="token-123",
    )
    mock_session = await app.dependency_overrides[get_async_session]()
    mock_session.scalar = AsyncMock(return_value="Acme")

    with (
        patch.object(organization_router, "OrgService") as mock_service_class,
        patch.object(config, "TRACECAT__EMAIL_DOMAIN", "mail.example.com"),
        patch.object(Mailer, "deliver") as mock_deliver,
    ):
        mock_service = AsyncMock()
        mock_service.organization_id = organization_id
        mock_service.create_invitation.return_value = invitation
        mock_service_class.return_value = mock_service

        response = client.post(
            "/organization/invitations",
            json={"email": invitation.email, "role_id": str(role_id)},
        )

    assert response.status_code == status.HTTP_201_CREATED
    mock_deliver.assert_called_once()
    message = mock_deliver.call_args.args[0]
    assert isinstance(message, OutboundEmail)
    assert message.to == (invitation.email,)
    assert message.subject == "Join Acme on Tracecat"
    assert message.from_addr == "Tracecat <no-reply@mail.example.com>"
    assert "token=token-123" in message.text


@pytest.mark.anyio
async def test_delete_organization_requires_owner_role(
    client: TestClient, test_admin_role: Role
) -> None:
    response = client.delete("/organization?confirm=Test%20Organization")
    assert response.status_code == status.HTTP_403_FORBIDDEN
