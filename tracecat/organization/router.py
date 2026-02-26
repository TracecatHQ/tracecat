from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.dependencies import OrgUserRole
from tracecat.auth.schemas import SessionRead, UserUpdate
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import (
    Organization,
    OrganizationDomain,
    OrganizationMembership,
    User,
    UserRoleAssignment,
)
from tracecat.db.models import (
    Role as DBRole,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import SessionID, UserID
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.service import InvitationService
from tracecat.organization.schemas import (
    OrgDomainRead,
    OrgMemberDetail,
    OrgMemberRead,
    OrgMemberStatus,
    OrgRead,
    UserWorkspaceMembership,
)
from tracecat.organization.service import OrgService
from tracecat.tiers.schemas import EffectiveEntitlements
from tracecat.tiers.service import TierService

router = APIRouter(prefix="/organization", tags=["organization"])


@router.get("", response_model=OrgRead)
@require_scope("org:read")
async def get_organization(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> OrgRead:
    """Get the current organization.

    Returns basic information about the organization the authenticated user belongs to.
    """
    if role.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No organization context",
        )

    result = await session.execute(
        select(Organization).where(Organization.id == role.organization_id)
    )
    org = result.scalar_one_or_none()

    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    return OrgRead(id=org.id, name=org.name)


@router.get("/domains", response_model=list[OrgDomainRead])
@require_scope("org:read")
async def list_organization_domains(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> list[OrgDomainRead]:
    """List domains assigned to the current organization."""
    if role.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No organization context",
        )

    stmt = (
        select(OrganizationDomain)
        .where(OrganizationDomain.organization_id == role.organization_id)
        .order_by(
            OrganizationDomain.is_primary.desc(),
            OrganizationDomain.created_at.asc(),
            OrganizationDomain.id.asc(),
        )
    )
    result = await session.execute(stmt)
    domains = result.scalars().all()

    return [
        OrgDomainRead(
            id=domain.id,
            organization_id=domain.organization_id,
            domain=domain.domain,
            normalized_domain=domain.normalized_domain,
            is_primary=domain.is_primary,
            is_active=domain.is_active,
            verified_at=domain.verified_at,
            verification_method=domain.verification_method,
            created_at=domain.created_at,
            updated_at=domain.updated_at,
        )
        for domain in domains
    ]


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:delete")
async def delete_organization(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    confirm: str | None = Query(
        default=None,
        description="Must exactly match the organization name.",
    ),
) -> None:
    """Delete the current organization.

    Restricted to organization owners and platform superusers.
    """
    service = OrgService(session, role=role)
    try:
        await service.delete_organization(confirmation=confirm)
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        ) from e
    except TracecatValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e


@router.get("/entitlements", response_model=EffectiveEntitlements)
async def get_organization_entitlements(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> EffectiveEntitlements:
    """Get the effective entitlements for the current organization."""
    if role.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No organization context",
        )

    tier_service = TierService(session)
    return await tier_service.get_effective_entitlements(role.organization_id)


@router.get("/members/me", response_model=OrgMemberDetail)
@require_scope("org:member:read")
async def get_current_org_member(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> OrgMemberDetail:
    """Get the current user's organization membership.

    Returns the organization membership details for the authenticated user,
    including their org role (member, admin, or owner).

    This endpoint doesn't require admin access - any authenticated org member
    can view their own membership details.
    """
    if role.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not authenticated",
        )
    if role.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No organization context",
        )

    # Query user and membership directly (no admin access required)
    statement = (
        select(User)
        .join(
            OrganizationMembership,
            OrganizationMembership.user_id == User.id,  # pyright: ignore[reportArgumentType]
        )
        .where(
            and_(
                User.id == role.user_id,  # pyright: ignore[reportArgumentType]
                OrganizationMembership.organization_id == role.organization_id,  # pyright: ignore[reportArgumentType]
            )
        )
    )
    result = await session.execute(statement)
    user = result.scalar_one_or_none()

    if user is None and role.is_platform_superuser:
        # Superusers have implicit owner access to all organizations
        user_result = await session.execute(
            select(User).where(User.id == role.user_id)  # pyright: ignore[reportArgumentType]
        )
        user = user_result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User is not a member of this organization",
        )

    # Get role name from RBAC assignment
    rbac_stmt = (
        select(DBRole.name)
        .join(UserRoleAssignment, UserRoleAssignment.role_id == DBRole.id)
        .where(
            UserRoleAssignment.user_id == user.id,
            UserRoleAssignment.organization_id == role.organization_id,
            UserRoleAssignment.workspace_id.is_(None),
        )
    )
    rbac_result = await session.execute(rbac_stmt)
    role_name = rbac_result.scalar_one_or_none()

    # Superusers always show as Owner
    if role_name is None and role.is_platform_superuser:
        role_name = "Organization Owner"

    return OrgMemberDetail(
        user_id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        role=role_name or "Member",
        is_active=user.is_active,
        is_verified=user.is_verified,
        last_login_at=user.last_login_at,
    )


@router.get("/members", response_model=list[OrgMemberRead])
@require_scope("org:member:read")
async def list_org_members(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> list[OrgMemberRead]:
    service = OrgService(session, role=role)
    members = await service.list_members()
    now = datetime.now(UTC)

    # Build a map of user_id -> RBAC role name for org-wide assignments
    user_ids = [user.id for user in members]
    rbac_stmt = (
        select(UserRoleAssignment.user_id, DBRole.name, DBRole.slug)
        .join(DBRole, UserRoleAssignment.role_id == DBRole.id)
        .where(
            UserRoleAssignment.organization_id == role.organization_id,
            UserRoleAssignment.workspace_id.is_(None),
            UserRoleAssignment.user_id.in_(user_ids),  # pyright: ignore[reportAttributeAccessIssue]
        )
    )
    rbac_result = await session.execute(rbac_stmt)
    rbac_map: dict[str, tuple[str, str | None]] = {
        str(user_id): (name, slug) for user_id, name, slug in rbac_result.tuples().all()
    }

    result: list[OrgMemberRead] = []
    for user in members:
        rbac_info = rbac_map.get(str(user.id))
        if rbac_info:
            role_name, role_slug = rbac_info
        else:
            role_name = "Member"
            role_slug = "organization-member"
        result.append(
            OrgMemberRead(
                user_id=user.id,
                email=user.email,
                role_name=role_name,
                role_slug=role_slug,
                status=OrgMemberStatus.ACTIVE
                if user.is_active
                else OrgMemberStatus.INACTIVE,
                first_name=user.first_name,
                last_name=user.last_name,
                last_login_at=user.last_login_at,
            )
        )

    # Add pending, non-expired invitations as "invited" members
    inv_service = InvitationService(session, role=role)
    invitations = await inv_service.list_org_invitations(
        status=InvitationStatus.PENDING
    )
    for inv in invitations:
        if inv.expires_at > now:
            result.append(
                OrgMemberRead(
                    invitation_id=inv.id,
                    email=inv.email,
                    role_name=inv.role_obj.name,
                    role_slug=inv.role_obj.slug,
                    status=OrgMemberStatus.INVITED,
                    expires_at=inv.expires_at,
                    created_at=inv.created_at,
                )
            )

    return result


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:member:remove")
async def delete_org_member(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    user_id: UserID,
) -> None:
    service = OrgService(session, role=role)
    try:
        await service.delete_member(user_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Action cannot be performed. Check if user is a superuser or has active sessions.",
        ) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
        ) from e


@router.patch("/members/{user_id}", response_model=OrgMemberDetail)
@require_scope("org:member:update")
async def update_org_member(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    user_id: UserID,
    params: UserUpdate,
) -> OrgMemberDetail:
    service = OrgService(session, role=role)
    try:
        user = await service.update_member(user_id, params)
        # Get role name from RBAC assignment
        rbac_stmt = (
            select(DBRole.name)
            .join(UserRoleAssignment, UserRoleAssignment.role_id == DBRole.id)
            .where(
                UserRoleAssignment.user_id == user.id,
                UserRoleAssignment.organization_id == role.organization_id,
                UserRoleAssignment.workspace_id.is_(None),
            )
        )
        rbac_result = await session.execute(rbac_stmt)
        role_name = rbac_result.scalar_one_or_none() or "Member"
        return OrgMemberDetail(
            user_id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            email=user.email,
            role=role_name,
            is_active=user.is_active,
            is_verified=user.is_verified,
            last_login_at=user.last_login_at,
        )
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        ) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
        ) from e


@router.get("/members/{user_id}/workspace-memberships")
@require_scope("org:member:read")
async def list_member_workspace_memberships(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    user_id: UserID,
) -> list[UserWorkspaceMembership]:
    """List workspace memberships for an organization member."""
    service = OrgService(session, role=role)
    memberships = await service.list_member_workspace_memberships(user_id)
    return [
        UserWorkspaceMembership(
            workspace_id=ws_id,
            workspace_name=ws_name,
            role_name=role_name or "Member",
        )
        for ws_id, ws_name, role_name in memberships
    ]


@router.get("/sessions", response_model=list[SessionRead])
@require_scope("org:member:read")
async def list_sessions(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> list[SessionRead]:
    service = OrgService(session, role=role)
    return await service.list_sessions()


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:member:remove")
async def delete_session(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    session_id: SessionID,
) -> None:
    service = OrgService(session, role=role)
    try:
        await service.delete_session(session_id)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        ) from e
