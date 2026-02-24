from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError

from tracecat.auth.credentials import (
    AuthenticatedUserOnly,
    OptionalUserDep,
    compute_effective_scopes,
)
from tracecat.auth.dependencies import OrgUserRole
from tracecat.auth.types import Role
from tracecat.auth.users import current_active_user
from tracecat.authz.controls import has_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import (
    Invitation,
    Organization,
    User,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import InvitationID, WorkspaceID
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.schemas import (
    InvitationAccept,
    InvitationCreate,
    InvitationRead,
    InvitationReadMinimal,
    PendingInvitationRead,
)
from tracecat.invitations.service import InvitationService, accept_invitation_for_user

router = APIRouter(prefix="/invitations", tags=["invitations"])


def _get_user_display_name_and_email(
    user: User | None,
) -> tuple[str | None, str | None]:
    """Build display name/email pair for inviter fields."""
    if user is None:
        return None, None
    if user.first_name or user.last_name:
        name_parts = [user.first_name, user.last_name]
        name = " ".join(part for part in name_parts if part)
    else:
        name = user.email
    return name, user.email


async def _check_scope(
    role: Role,
    required_scope: str,
    workspace_id: WorkspaceID | None = None,
) -> None:
    """Verify the user has the required scope, checking org then workspace scopes.

    1. Check org-level scopes already on the role (free).
    2. For workspace ops, compute workspace-specific scopes (cached 30s TTL).

    Raises:
        HTTPException 403 if the user lacks the required scope.
    """
    # Org-level scopes are already resolved on the role
    if role.scopes and has_scope(role.scopes, required_scope):
        return

    # For workspace operations, compute workspace-specific scopes
    if workspace_id is not None:
        ws_role = role.model_copy(update={"workspace_id": workspace_id})
        ws_scopes = await compute_effective_scopes(ws_role)
        if has_scope(ws_scopes, required_scope):
            return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Insufficient permissions",
    )


def _invitation_to_read(inv: Invitation) -> InvitationRead:
    """Convert an Invitation DB model to InvitationRead response."""
    return InvitationRead(
        id=inv.id,
        organization_id=inv.organization_id,
        workspace_id=inv.workspace_id,
        email=inv.email,
        role_id=inv.role_id,
        role_name=inv.role_obj.name,
        role_slug=inv.role_obj.slug,
        status=inv.status,
        invited_by=inv.invited_by,
        expires_at=inv.expires_at,
        created_at=inv.created_at,
        accepted_at=inv.accepted_at,
        token=inv.token,
    )


# === Unified create/list endpoints ===


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_invitation(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: InvitationCreate,
) -> InvitationRead | None:
    """Create an invitation (org or workspace, based on workspace_id in body).

    When ``workspace_id`` is set, creates a workspace invitation (smart: direct
    membership if user is an org member, else invitation).  Returns ``null``
    when the user was added directly as a member (no invitation needed).
    When ``workspace_id`` is None, creates an org-level invitation.
    """
    service = InvitationService(session, role=role)

    if params.workspace_id is not None:
        await _check_scope(role, "workspace:member:invite", params.workspace_id)
        try:
            invitation = await service.create_workspace_invitation(
                params.workspace_id, params
            )
            if invitation is None:
                return None
            return _invitation_to_read(invitation)
        except TracecatAuthorizationError as e:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(e)
            ) from e
        except TracecatValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(e)
            ) from e
        except IntegrityError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Member already exists in this workspace",
            ) from e
    else:
        await _check_scope(role, "org:member:invite")
        try:
            workspace_assignments = (
                [(wa.workspace_id, wa.role_id) for wa in params.workspace_assignments]
                if params.workspace_assignments
                else None
            )
            invitation = await service.create_org_invitation(
                email=params.email,
                role_id=params.role_id,
                workspace_assignments=workspace_assignments,
            )
        except TracecatAuthorizationError as e:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(e)
            ) from e
        except TracecatValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
            ) from e
        except IntegrityError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An invitation already exists for this email",
            ) from e
        return _invitation_to_read(invitation)


@router.get("")
async def list_invitations(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    workspace_id: WorkspaceID | None = Query(None),
    invitation_status: InvitationStatus | None = Query(None, alias="status"),
) -> list[InvitationRead]:
    """List invitations (org or workspace, based on workspace_id query param)."""
    service = InvitationService(session, role=role)

    if workspace_id is not None:
        await _check_scope(role, "workspace:member:read", workspace_id)
        invitations = await service.list_workspace_invitations(
            workspace_id, status=invitation_status
        )
    else:
        await _check_scope(role, "org:member:read")
        invitations = await service.list_org_invitations(status=invitation_status)

    return [_invitation_to_read(inv) for inv in invitations]


# NOTE: /pending/me MUST be declared before /{invitation_id} routes
@router.get("/pending/me")
async def list_my_pending_invitations(
    *,
    role: AuthenticatedUserOnly,
    session: AsyncDBSession,
    user: Annotated[User, Depends(current_active_user)],
) -> list[PendingInvitationRead]:
    """List pending, unexpired invitations for the authenticated user.

    Returns both org-level and workspace-level invitations.
    """
    from datetime import UTC, datetime

    assert role.user_id is not None

    now = datetime.now(UTC)
    statement = (
        select(Invitation, Organization, User, DBRole, Workspace)
        .join(
            Organization,
            Organization.id == Invitation.organization_id,  # pyright: ignore[reportArgumentType]
        )
        .join(
            DBRole,
            DBRole.id == Invitation.role_id,  # pyright: ignore[reportArgumentType]
        )
        .outerjoin(
            User,
            User.id == Invitation.invited_by,  # pyright: ignore[reportArgumentType]
        )
        .outerjoin(
            Workspace,
            Workspace.id == Invitation.workspace_id,  # pyright: ignore[reportArgumentType]
        )
        .where(
            func.lower(Invitation.email) == user.email.lower(),
            Invitation.status == InvitationStatus.PENDING,
            Invitation.expires_at > now,
        )
        .order_by(Invitation.created_at.desc())
    )
    result = await session.execute(statement)
    rows = result.tuples().all()

    pending: list[PendingInvitationRead] = []
    for invitation, organization, inviter, role_obj, workspace in rows:
        inviter_name, inviter_email = _get_user_display_name_and_email(inviter)
        pending.append(
            PendingInvitationRead(
                token=invitation.token,
                organization_id=invitation.organization_id,
                organization_name=organization.name,
                workspace_id=invitation.workspace_id,
                workspace_name=workspace.name if workspace else None,
                inviter_name=inviter_name,
                inviter_email=inviter_email,
                role_name=role_obj.name,
                role_slug=role_obj.slug,
                expires_at=invitation.expires_at,
            )
        )
    return pending


# === Existing endpoints ===


@router.get("/token/{token}", response_model=InvitationReadMinimal)
async def get_invitation_by_token(
    *,
    session: AsyncDBSession,
    token: str,
    user: OptionalUserDep = None,
) -> InvitationReadMinimal:
    """Get invitation details by token (public endpoint for the accept page).

    Works for both org-level and workspace-level invitations.
    Returns organization/workspace name and inviter info.
    If user is authenticated, also returns whether their email matches.
    """
    result = await session.execute(
        select(Invitation, DBRole)
        .join(DBRole, cast(DBRole.id, UUID) == Invitation.role_id)
        .where(Invitation.token == token)
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found",
        )
    invitation, role_obj = row

    # Fetch organization
    org_result = await session.execute(
        select(Organization).where(Organization.id == invitation.organization_id)
    )
    org = org_result.scalar_one()

    # Fetch workspace if workspace-scoped
    workspace_name: str | None = None
    if invitation.workspace_id is not None:
        ws_result = await session.execute(
            select(Workspace).where(Workspace.id == invitation.workspace_id)
        )
        workspace = ws_result.scalar_one()
        workspace_name = workspace.name

    # Fetch inviter info
    inviter_name: str | None = None
    inviter_email: str | None = None
    if invitation.invited_by:
        inviter_result = await session.execute(
            select(User).where(cast(User.id, UUID) == invitation.invited_by)
        )
        inviter = inviter_result.scalar_one_or_none()
        inviter_name, inviter_email = _get_user_display_name_and_email(inviter)

    # Check email match for authenticated user
    email_matches: bool | None = None
    if user is not None:
        email_matches = user.email.lower() == invitation.email.lower()

    return InvitationReadMinimal(
        invitation_id=invitation.id,
        organization_id=invitation.organization_id,
        organization_name=org.name,
        workspace_id=invitation.workspace_id,
        workspace_name=workspace_name,
        inviter_name=inviter_name,
        inviter_email=inviter_email,
        role_name=role_obj.name,
        role_slug=role_obj.slug,
        status=invitation.status,
        expires_at=invitation.expires_at,
        email_matches=email_matches,
    )


@router.post("/accept")
async def accept_invitation(
    *,
    role: AuthenticatedUserOnly,
    session: AsyncDBSession,
    params: InvitationAccept,
) -> dict[str, str]:
    """Accept an invitation (org or workspace) and join.

    Determines the invitation type from the token and creates the appropriate
    memberships. Uses AuthenticatedUserOnly — no org context required since
    the user may not belong to the org yet.
    """
    assert role.user_id is not None
    try:
        await accept_invitation_for_user(
            session,
            user_id=role.user_id,
            token=params.token,
        )
        return {"message": "Invitation accepted successfully"}
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (TracecatAuthorizationError, TracecatValidationError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member",
        ) from e


# NOTE: parametric routes must come after /pending/me, /token/{token}, /accept


@router.delete("/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    invitation_id: InvitationID,
) -> None:
    """Revoke any pending invitation (org or workspace)."""
    service = InvitationService(session, role=role)
    try:
        invitation = await service.get_invitation(invitation_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    if invitation.workspace_id is not None:
        await _check_scope(role, "workspace:member:invite", invitation.workspace_id)
    else:
        await _check_scope(role, "org:member:invite")

    try:
        await service.revoke_invitation(invitation_id)
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.get("/{invitation_id}/token")
async def get_invitation_token(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    invitation_id: InvitationID,
) -> dict[str, str]:
    """Get the token for a specific invitation (admin only).

    Used to generate shareable invitation links.
    """
    service = InvitationService(session, role=role)
    try:
        invitation = await service.get_invitation(invitation_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    if invitation.workspace_id is not None:
        await _check_scope(role, "workspace:member:invite", invitation.workspace_id)
    else:
        await _check_scope(role, "org:member:invite")

    return {"token": invitation.token}
