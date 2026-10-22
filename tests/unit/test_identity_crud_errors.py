"""Regression coverage for identity CRUD error boundaries."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Literal, get_args
from unittest.mock import AsyncMock

import pytest
from asyncpg import ForeignKeyViolationError, UniqueViolationError
from fastapi import FastAPI
from fastapi_users.exceptions import UserNotExists
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.api.common import generic_exception_handler
from tracecat.auth.credentials import AuthenticatedUserOnly
from tracecat.auth.dependencies import OrgActorRole, OrgUserRole
from tracecat.auth.schemas import UserUpdate
from tracecat.auth.types import Role
from tracecat.auth.users import UserManager
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session, get_async_session_bypass_rls
from tracecat.db.models import Invitation, OrganizationMembership, User
from tracecat.exceptions import TracecatConflictError
from tracecat.invitations import router as invitation_router
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.service import InvitationService
from tracecat.organization import router as organization_router
from tracecat.organization.management import create_organization
from tracecat.organization.service import OrgService
from tracecat.workspaces import router as workspace_router
from tracecat.workspaces.router import WorkspaceUserInPath

pytestmark = pytest.mark.anyio


def _integrity_error(constraint: str | None) -> IntegrityError:
    original = (
        UniqueViolationError.new({"C": "23505", "n": constraint, "M": "synthetic"})
        if constraint is not None
        else ForeignKeyViolationError("synthetic database detail")
    )
    return IntegrityError("synthetic statement", {}, original)


@pytest.fixture
async def client(
    session: AsyncSession, svc_admin_role: Role
) -> AsyncIterator[AsyncClient]:
    role = svc_admin_role.model_copy(update={"scopes": frozenset({"*"})})
    token = ctx_role.set(role)
    app = FastAPI()
    app.add_exception_handler(Exception, generic_exception_handler)
    for router in (
        workspace_router.router,
        invitation_router.router,
        organization_router.router,
    ):
        app.include_router(router)
    for alias in (
        OrgActorRole,
        OrgUserRole,
        WorkspaceUserInPath,
        AuthenticatedUserOnly,
    ):
        app.dependency_overrides[get_args(alias)[1].dependency] = lambda: role
    app.dependency_overrides[get_async_session] = lambda: session
    app.dependency_overrides[get_async_session_bypass_rls] = lambda: session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as http:
            yield http
    finally:
        ctx_role.reset(token)


@pytest.mark.parametrize(
    ("surface", "constraint"),
    [
        ("workspace", "uq_workspace_id"),
        ("workspace_membership", "uq_user_role_assignment_user_id_workspace_id"),
        ("workspace_membership", "pk_membership"),
        ("invitation", "ix_invitation_org_email_pending_unique"),
        ("accept_invitation", "pk_organization_membership"),
        ("accept_invitation", "pk_membership"),
        ("accept_invitation", "uq_user_role_assignment_user_id_workspace_id"),
        ("accept_invitation", "ix_user_role_assignment_user_org_unique"),
    ],
)
@pytest.mark.parametrize("error_kind", ["known", "unrelated_unique", "foreign_key"])
async def test_only_named_duplicates_become_conflicts(
    client: AsyncClient,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    constraint: str,
    error_kind: Literal["known", "unrelated_unique", "foreign_key"],
) -> None:
    known = error_kind == "known"
    match error_kind:
        case "known":
            error = _integrity_error(constraint)
        case "unrelated_unique":
            error = _integrity_error("uq_unrelated_synthetic")
        case "foreign_key":
            error = _integrity_error(None)
    rollback = AsyncMock(wraps=session.rollback)
    monkeypatch.setattr(session, "rollback", rollback)
    user_id, workspace_id = uuid.uuid4(), uuid.uuid4()
    match surface:
        case "workspace":
            monkeypatch.setattr(
                workspace_router.WorkspaceService,
                "create_workspace",
                AsyncMock(side_effect=error),
            )
            response = await client.post(
                "/workspaces", json={"name": "Synthetic workspace"}
            )
        case "workspace_membership":
            monkeypatch.setattr(
                workspace_router.MembershipService,
                "create_membership",
                AsyncMock(side_effect=error),
            )
            response = await client.post(
                f"/workspaces/{workspace_id}/memberships",
                json={"user_id": str(user_id)},
            )
        case "invitation":
            monkeypatch.setattr(
                InvitationService, "create_invitation", AsyncMock(side_effect=error)
            )
            response = await client.post(
                "/invitations",
                json={
                    "email": "synthetic@example.com",
                    "grants": [{"role_id": str(uuid.uuid4())}],
                },
            )
        case "accept_invitation":
            monkeypatch.setattr(
                invitation_router,
                "accept_invitation_for_user",
                AsyncMock(side_effect=error),
            )
            response = await client.post(
                "/invitations/accept", json={"token": "synthetic"}
            )
        case _:
            raise AssertionError("Unknown test surface")
    assert response.status_code == (409 if known else 500), response.text
    assert "synthetic database detail" not in response.text
    assert "synthetic statement" not in response.text
    if not known:
        assert response.json() == {
            "message": "An unexpected error occurred. Please try again later."
        }
    rollback.assert_awaited_once()


async def test_member_delete_unknown_integrity_is_server_error(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    rollback = AsyncMock(wraps=session.rollback)
    monkeypatch.setattr(session, "rollback", rollback)
    monkeypatch.setattr(
        OrgService, "delete_member", AsyncMock(side_effect=_integrity_error(None))
    )
    response = await client.delete(f"/organization/members/{uuid.uuid4()}")
    assert response.status_code == 500
    assert response.json() == {
        "message": "An unexpected error occurred. Please try again later."
    }
    rollback.assert_awaited_once()


@pytest.fixture
async def members(session: AsyncSession, svc_admin_role: Role) -> tuple[User, User]:
    members = tuple(
        User(
            id=uuid.uuid4(),
            email=f"member-{uuid.uuid4().hex}@example.com",
            hashed_password="synthetic",
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        for _ in range(2)
    )
    session.add_all(members)
    await session.flush()
    session.add_all(
        OrganizationMembership(
            user_id=user.id, organization_id=svc_admin_role.organization_id
        )
        for user in members
    )
    await session.commit()
    return members[0], members[1]


@pytest.mark.parametrize("race", [False, True])
async def test_duplicate_member_email_is_conflict(
    client: AsyncClient,
    session: AsyncSession,
    members: tuple[User, User],
    monkeypatch: pytest.MonkeyPatch,
    race: bool,
) -> None:
    first, second = members
    first_id, second_email = first.id, second.email
    if race:
        # Bypass the optimistic read: the real PostgreSQL email index must win.
        monkeypatch.setattr(
            UserManager, "get_by_email", AsyncMock(side_effect=UserNotExists())
        )
    response = await client.patch(
        f"/organization/members/{first_id}", json={"email": second_email}
    )
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "A user with this email already exists"}
    session.expire_all()
    updated = await session.get(User, first_id)
    assert updated is not None and updated.email != second_email


async def test_update_member_unrelated_integrity_propagates(
    session: AsyncSession,
    members: tuple[User, User],
    svc_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = _integrity_error("uq_unrelated_synthetic")
    user_id = members[0].id
    monkeypatch.setattr(UserManager, "update", AsyncMock(side_effect=error))
    token = ctx_role.set(svc_admin_role)
    try:
        with pytest.raises(IntegrityError) as raised:
            await OrgService(session, role=svc_admin_role).update_member(
                user_id, UserUpdate(first_name="Synthetic")
            )
        assert raised.value is error
        assert await session.get(User, user_id) is not None
    finally:
        ctx_role.reset(token)


@pytest.mark.parametrize(
    "current_status", [InvitationStatus.ACCEPTED, InvitationStatus.REVOKED]
)
async def test_revoke_settled_invitation_is_conflict(
    client: AsyncClient,
    session: AsyncSession,
    svc_admin_role: Role,
    current_status: InvitationStatus,
) -> None:
    invitation = Invitation(
        organization_id=svc_admin_role.organization_id,
        email="settled@example.com",
        token=uuid.uuid4().hex,
        status=current_status,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(invitation)
    await session.commit()
    response = await client.delete(f"/invitations/{invitation.id}")
    assert response.status_code == 409, response.text
    await session.refresh(invitation)
    assert invitation.status == current_status


async def test_revoke_missing_invitation_is_not_found(client: AsyncClient) -> None:
    response = await client.delete(f"/invitations/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_membership_domain_conflict_still_returns_conflict(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workspace_router.MembershipService,
        "create_membership",
        AsyncMock(
            side_effect=TracecatConflictError("User is already a member of workspace.")
        ),
    )
    response = await client.post(
        f"/workspaces/{uuid.uuid4()}/memberships", json={"user_id": str(uuid.uuid4())}
    )
    assert response.status_code == 409


async def test_duplicate_organization_slug_is_typed_conflict(
    session: AsyncSession,
) -> None:
    slug = uuid.uuid4().hex
    await create_organization(session, name="Synthetic organization", slug=slug)
    with pytest.raises(TracecatConflictError, match="already exists"):
        await create_organization(
            session, name="Another synthetic organization", slug=slug
        )


async def test_organization_creation_unknown_integrity_propagates(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = _integrity_error("uq_unrelated_synthetic")
    monkeypatch.setattr(session, "flush", AsyncMock(side_effect=error))
    rollback = AsyncMock(wraps=session.rollback)
    monkeypatch.setattr(session, "rollback", rollback)
    with pytest.raises(IntegrityError) as raised:
        await create_organization(
            session, name="Synthetic organization", slug=uuid.uuid4().hex
        )
    assert raised.value is error
    rollback.assert_awaited_once()


@pytest.mark.parametrize("missing", [True, False])
async def test_workspace_delete_distinguishes_missing_from_last_workspace(
    client: AsyncClient,
    svc_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    missing: bool,
) -> None:
    monkeypatch.setattr(
        workspace_router.WorkspaceService,
        "admin_list_workspaces",
        AsyncMock(return_value=[object()]),
    )
    workspace_id = uuid.uuid4() if missing else svc_admin_role.workspace_id
    response = await client.delete(f"/workspaces/{workspace_id}")
    assert response.status_code == (404 if missing else 400), response.text
