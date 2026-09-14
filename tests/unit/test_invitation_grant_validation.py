"""Isolated checks for batched invitation validation and live scope ceilings."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from tracecat.auth.types import Role
from tracecat.db.models import Invitation, Scope
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import TracecatAuthorizationError, TracecatValidationError
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.schemas import InvitationCreate, InvitationGrant
from tracecat.invitations.service import (
    revoke_invitation_row,
    validate_grants,
)


@pytest.fixture(scope="session")
def default_org() -> None:
    """These tests use a mocked session and require no seeded database."""


@pytest.fixture
def clean_redis_db() -> None:
    """Validation does not use Redis."""


@pytest.fixture(scope="session")
def workflow_bucket() -> None:
    """Validation does not use object storage."""


@pytest.mark.anyio
@pytest.mark.parametrize("grant_count", [1, 4])
@pytest.mark.parametrize(
    ("live_scopes", "superuser", "missing_role"),
    [
        (["workflow:read", "workflow:execute"], False, False),
        (["workflow:read"], False, False),
        ([], False, False),
        ([], True, False),
        (["workflow:read"], False, True),
        ([], True, True),
    ],
)
async def test_validate_grants_batch(
    grant_count: int,
    live_scopes: list[str],
    superuser: bool,
    missing_role: bool,
) -> None:
    organization_id = uuid.uuid4()
    granter = Role(
        type="user",
        service_id="tracecat-api",
        user_id=uuid.uuid4(),
        organization_id=organization_id,
        is_platform_superuser=superuser,
        scopes=frozenset({"*"}),  # Stale cached privileges must not grant access.
    )
    targets = [
        DBRole(
            id=uuid.uuid4(),
            organization_id=organization_id,
            name=f"Role {index}",
            scopes=[Scope(name=scope_name)],
        )
        for index, scope_name in enumerate(
            ["workflow:read", "workflow:execute"][:grant_count]
        )
    ]
    params = InvitationCreate(
        email="invitee@example.com",
        grants=[
            InvitationGrant(
                workspace_id=uuid.uuid4(), role_id=targets[index % len(targets)].id
            )
            for index in range(grant_count)
        ],
    )
    workspace_ids = {grant.workspace_id for grant in params.grants}
    workspace_result = MagicMock()
    workspace_result.scalars.return_value.all.return_value = list(workspace_ids)
    role_result = MagicMock()
    # A missing or foreign-organization role is absent from the filtered query.
    role_result.scalars.return_value.all.return_value = (
        targets[:-1] if missing_role else targets
    )
    scope_result = MagicMock()
    scope_result.scalars.return_value.all.return_value = live_scopes
    session = AsyncMock()
    session.execute.side_effect = [workspace_result, role_result, scope_result]

    if missing_role:
        with pytest.raises(TracecatValidationError, match="Invalid role ID"):
            await validate_grants(session, granter, organization_id, params)
    elif not superuser and any(
        scope.name not in live_scopes for target in targets for scope in target.scopes
    ):
        with pytest.raises(TracecatAuthorizationError, match="Cannot grant scopes"):
            await validate_grants(session, granter, organization_id, params)
    else:
        await validate_grants(session, granter, organization_id, params)

    # Role/workspace lookups and the live ceiling query do not grow per grant.
    assert session.execute.await_count == (2 if superuser or missing_role else 3)
    role_query = session.execute.await_args_list[1].args[0].compile()
    assert organization_id in role_query.params.values()
    assert any(
        isinstance(value, list) and set(value) == {target.id for target in targets}
        for value in role_query.params.values()
    )
    assert "role.organization_id =" in str(role_query)
    assert "role.id IN" in str(role_query)


@pytest.mark.anyio
async def test_validate_grants_rejects_foreign_workspace() -> None:
    organization_id = uuid.uuid4()
    granter = Role(
        type="user", service_id="tracecat-api", organization_id=organization_id
    )
    params = InvitationCreate(
        email="invitee@example.com",
        grants=[InvitationGrant(workspace_id=uuid.uuid4(), role_id=uuid.uuid4())],
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute.return_value = result

    with pytest.raises(TracecatValidationError, match="Workspace not found"):
        await validate_grants(session, granter, organization_id, params)
    assert session.execute.await_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("current_status", list(InvitationStatus))
async def test_revoke_uses_database_status(current_status: InvitationStatus) -> None:
    invitation = Invitation(id=uuid.uuid4(), status=InvitationStatus.PENDING)
    session = AsyncMock()
    result = MagicMock()
    changed = current_status == InvitationStatus.PENDING
    result.scalar_one_or_none.return_value = invitation.id if changed else None
    session.execute.return_value = result

    async def refresh(row: Invitation) -> None:
        row.status = InvitationStatus.REVOKED if changed else current_status

    session.refresh.side_effect = refresh
    if changed:
        await revoke_invitation_row(session, invitation)
        assert invitation.status == InvitationStatus.REVOKED
    else:
        with pytest.raises(TracecatAuthorizationError, match=current_status.value):
            await revoke_invitation_row(session, invitation)
        assert invitation.status == current_status

    query = session.execute.await_args.args[0]
    compiled = query.compile()
    assert "invitation.status =" in str(compiled).split("WHERE")[1]
    assert InvitationStatus.PENDING in compiled.params.values()
    assert query.get_execution_options()["synchronize_session"] is False
