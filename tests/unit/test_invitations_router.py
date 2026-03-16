from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from tracecat.auth.types import Role
from tracecat.invitations.router import (
    create_invitation,
    get_invitation_token,
    revoke_invitation,
)
from tracecat.invitations.schemas import InvitationCreate


def _role() -> Role:
    return Role(
        type="user",
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


@pytest.mark.anyio
async def test_create_invitation_translates_integrity_error_to_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    check_scope = AsyncMock()
    create_invitation_mock = AsyncMock(
        side_effect=IntegrityError("", {}, Exception("duplicate invitation"))
    )
    monkeypatch.setattr("tracecat.invitations.router._check_scope", check_scope)
    monkeypatch.setattr(
        "tracecat.invitations.router.InvitationService.create_invitation",
        create_invitation_mock,
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_invitation(
            role=_role(),
            session=session,
            params=InvitationCreate(
                email="invitee@example.com",
                role_id=uuid.uuid4(),
            ),
        )

    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.detail == "An invitation already exists for invitee@example.com"
    )
    session.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_revoke_invitation_uses_invite_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invitation = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
    )
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=invitation)
    scoped_role = _role()
    scoped_role = scoped_role.model_copy(
        update={"organization_id": invitation.organization_id}
    )
    scoped_role_for_context = AsyncMock(return_value=scoped_role)
    check_scope = AsyncMock()
    revoke_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "tracecat.invitations.router._scoped_role_for_org_context",
        scoped_role_for_context,
    )
    monkeypatch.setattr("tracecat.invitations.router._check_scope", check_scope)
    monkeypatch.setattr(
        "tracecat.invitations.router.InvitationService.revoke_invitation",
        revoke_mock,
    )

    await revoke_invitation(
        role=_role(),
        session=session,
        invitation_id=invitation.id,
    )

    check_scope.assert_awaited_once_with(
        scoped_role,
        "workspace:member:invite",
        invitation.workspace_id,
    )


@pytest.mark.anyio
async def test_get_invitation_token_checks_scope_before_service_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invitation = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        token="secret-token",
    )
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=invitation)
    scoped_role = _role().model_copy(
        update={"organization_id": invitation.organization_id}
    )
    scoped_role_for_context = AsyncMock(return_value=scoped_role)
    check_scope = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="Insufficient permissions")
    )
    get_invitation_mock = AsyncMock()
    monkeypatch.setattr(
        "tracecat.invitations.router._scoped_role_for_org_context",
        scoped_role_for_context,
    )
    monkeypatch.setattr("tracecat.invitations.router._check_scope", check_scope)
    monkeypatch.setattr(
        "tracecat.invitations.router.InvitationService.get_invitation",
        get_invitation_mock,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_invitation_token(
            role=_role(),
            session=session,
            invitation_id=invitation.id,
        )

    assert exc_info.value.status_code == 403
    get_invitation_mock.assert_not_awaited()
