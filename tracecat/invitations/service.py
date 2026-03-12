from __future__ import annotations

import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import selectinload

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.authz.controls import has_scope
from tracecat.authz.service import invalidate_authz_caches
from tracecat.db.models import (
    GroupMember,
    GroupRoleAssignment,
    Invitation,
    Membership,
    OrganizationMembership,
    RoleScope,
    Scope,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import InvitationID, UserID, WorkspaceID
from tracecat.invitations.enums import InvitationStatus
from tracecat.service import BaseOrgService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tracecat.invitations.schemas import (
        InvitationCreate,
    )


@dataclass(frozen=True)
class InvitationGroup:
    """Resolved invitation group for token lookup and admin listing."""

    invitation: Invitation
    workspace_invitations: list[Invitation]
    accept_token: str
    redirected: bool = False


def _generate_invitation_token() -> str:
    """Generate a unique 64-character token for invitation magic links."""
    return secrets.token_urlsafe(48)[:64]


async def _compute_workspace_effective_scopes(
    session: AsyncSession,
    *,
    role: Role,
    workspace_id: WorkspaceID,
) -> frozenset[str]:
    """Compute effective scopes in a workspace context."""
    if role.is_platform_superuser:
        return frozenset({"*"})

    base_scopes = role.scopes or frozenset()
    if role.type != "user" or role.user_id is None or role.organization_id is None:
        return base_scopes

    user_scopes_stmt = (
        select(Scope.name)
        .join(RoleScope, RoleScope.scope_id == Scope.id)
        .join(DBRole, DBRole.id == RoleScope.role_id)
        .join(UserRoleAssignment, UserRoleAssignment.role_id == DBRole.id)
        .where(
            UserRoleAssignment.user_id == role.user_id,
            UserRoleAssignment.organization_id == role.organization_id,
            UserRoleAssignment.workspace_id == workspace_id,
        )
    )

    group_scopes_stmt = (
        select(Scope.name)
        .join(RoleScope, RoleScope.scope_id == Scope.id)
        .join(DBRole, DBRole.id == RoleScope.role_id)
        .join(GroupRoleAssignment, GroupRoleAssignment.role_id == DBRole.id)
        .join(GroupMember, GroupMember.group_id == GroupRoleAssignment.group_id)
        .where(
            GroupMember.user_id == role.user_id,
            GroupRoleAssignment.organization_id == role.organization_id,
            GroupRoleAssignment.workspace_id == workspace_id,
        )
    )

    scoped_result = await session.execute(user_scopes_stmt.union(group_scopes_stmt))
    scoped_scopes = frozenset(scoped_result.scalars().all())
    return frozenset(base_scopes | scoped_scopes)


async def _load_user_by_id(session: AsyncSession, *, user_id: UserID) -> User:
    result = await session.execute(select(User).where(cast(User.id, UUID) == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise TracecatAuthorizationError("User not found")
    return user


async def _ensure_org_membership(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: UserID,
) -> OrganizationMembership:
    result = await session.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
        )
    )
    if (membership := result.scalar_one_or_none()) is not None:
        return membership

    membership = OrganizationMembership(
        organization_id=organization_id,
        user_id=user_id,
    )
    session.add(membership)
    await session.flush()
    return membership


async def _ensure_workspace_membership(
    session: AsyncSession,
    *,
    workspace_id: WorkspaceID,
    user_id: UserID,
) -> Membership:
    result = await session.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user_id,
        )
    )
    if (membership := result.scalar_one_or_none()) is not None:
        return membership

    membership = Membership(workspace_id=workspace_id, user_id=user_id)
    session.add(membership)
    await session.flush()
    return membership


async def _upsert_user_role_assignment(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: UserID,
    workspace_id: WorkspaceID | None,
    role_id: uuid.UUID,
    replace_existing: bool = True,
) -> UserRoleAssignment:
    await _ensure_org_membership(
        session,
        organization_id=organization_id,
        user_id=user_id,
    )
    if workspace_id is not None:
        await _ensure_workspace_membership(
            session,
            workspace_id=workspace_id,
            user_id=user_id,
        )

    result = await session.execute(
        select(UserRoleAssignment).where(
            UserRoleAssignment.organization_id == organization_id,
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.workspace_id == workspace_id,
        )
    )
    assignment = result.scalar_one_or_none()
    if assignment is None:
        assignment = UserRoleAssignment(
            organization_id=organization_id,
            user_id=user_id,
            workspace_id=workspace_id,
            role_id=role_id,
        )
        session.add(assignment)
        return assignment
    if replace_existing and assignment.role_id != role_id:
        assignment.role_id = role_id
    return assignment


async def _resolve_role_for_scope(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    role_id: uuid.UUID,
    workspace_id: WorkspaceID | None,
) -> DBRole:
    result = await session.execute(
        select(DBRole).where(
            DBRole.id == role_id,
            DBRole.organization_id == organization_id,
        )
    )
    role_obj = result.scalar_one_or_none()
    if role_obj is None:
        raise TracecatValidationError("Invalid role ID for this organization")

    if workspace_id is None:
        if role_obj.slug is not None and role_obj.slug.startswith("workspace-"):
            raise TracecatValidationError(
                "Organization invitations require an org role"
            )
    else:
        if role_obj.slug is not None and role_obj.slug.startswith("organization-"):
            raise TracecatValidationError(
                "Workspace invitations require a workspace role"
            )
    return role_obj


async def _replace_existing_invitation(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    email: str,
    workspace_id: WorkspaceID | None,
) -> None:
    statement = select(Invitation).where(
        Invitation.organization_id == organization_id,
        func.lower(Invitation.email) == email.lower(),
        Invitation.workspace_id == workspace_id,
    )
    result = await session.execute(statement)
    existing = result.scalar_one_or_none()
    if existing is None:
        return

    if (
        existing.status == InvitationStatus.PENDING
        and existing.expires_at >= datetime.now(UTC)
    ):
        raise TracecatValidationError(f"An invitation already exists for {email}")

    await session.delete(existing)
    await session.flush()


async def _create_invitation_row(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    email: str,
    role_id: uuid.UUID,
    workspace_id: WorkspaceID | None,
    invited_by: UserID | None,
    expires_at: datetime,
) -> Invitation:
    await _replace_existing_invitation(
        session,
        organization_id=organization_id,
        email=email,
        workspace_id=workspace_id,
    )
    invitation = Invitation(
        organization_id=organization_id,
        workspace_id=workspace_id,
        email=email,
        role_id=role_id,
        invited_by=invited_by,
        token=_generate_invitation_token(),
        expires_at=expires_at,
        status=InvitationStatus.PENDING,
    )
    session.add(invitation)
    await session.flush()
    return invitation


async def _load_pending_workspace_options(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    email: str,
) -> list[Invitation]:
    result = await session.execute(
        select(Invitation)
        .where(
            Invitation.organization_id == organization_id,
            Invitation.workspace_id.is_not(None),
            func.lower(Invitation.email) == email.lower(),
            Invitation.status == InvitationStatus.PENDING,
        )
        .options(
            selectinload(Invitation.role_obj),
            selectinload(Invitation.workspace),
        )
        .order_by(Invitation.created_at.asc())
    )
    return list(result.scalars().all())


async def _build_group_from_org_invitation(
    session: AsyncSession,
    *,
    invitation: Invitation,
    accept_token: str | None = None,
    redirected: bool = False,
) -> InvitationGroup:
    workspace_invitations = await _load_pending_workspace_options(
        session,
        organization_id=invitation.organization_id,
        email=invitation.email,
    )
    return InvitationGroup(
        invitation=invitation,
        workspace_invitations=workspace_invitations,
        accept_token=accept_token or invitation.token,
        redirected=redirected,
    )


async def _resolve_group_by_token(
    session: AsyncSession,
    *,
    token: str,
) -> InvitationGroup:
    result = await session.execute(
        select(Invitation)
        .where(Invitation.token == token)
        .options(
            selectinload(Invitation.organization),
            selectinload(Invitation.inviter),
            selectinload(Invitation.role_obj),
            selectinload(Invitation.workspace),
        )
    )
    invitation = result.scalar_one_or_none()
    if invitation is None:
        raise TracecatNotFoundError("Invitation not found")

    if (
        invitation.workspace_id is None
        and invitation.status == InvitationStatus.PENDING
    ):
        return await _build_group_from_org_invitation(session, invitation=invitation)

    if (
        invitation.workspace_id is not None
        and invitation.status == InvitationStatus.PENDING
    ):
        org_result = await session.execute(
            select(Invitation)
            .where(
                Invitation.organization_id == invitation.organization_id,
                Invitation.workspace_id.is_(None),
                func.lower(Invitation.email) == invitation.email.lower(),
                Invitation.status == InvitationStatus.PENDING,
            )
            .options(
                selectinload(Invitation.organization),
                selectinload(Invitation.inviter),
                selectinload(Invitation.role_obj),
                selectinload(Invitation.workspace),
            )
            .order_by(Invitation.created_at.desc())
        )
        org_invitation = org_result.scalars().first()
        if org_invitation is not None:
            return await _build_group_from_org_invitation(
                session,
                invitation=org_invitation,
                accept_token=org_invitation.token,
                redirected=True,
            )

    return InvitationGroup(
        invitation=invitation,
        workspace_invitations=[],
        accept_token=invitation.token,
    )


def _group_invitations(
    invitations: Sequence[Invitation],
) -> list[InvitationGroup]:
    pending_orgs_by_key: dict[tuple[uuid.UUID, str], Invitation] = {}
    for invitation in invitations:
        if (
            invitation.workspace_id is None
            and invitation.status == InvitationStatus.PENDING
        ):
            pending_orgs_by_key[
                (invitation.organization_id, invitation.email.lower())
            ] = invitation

    workspace_rows_by_key: dict[tuple[uuid.UUID, str], list[Invitation]] = {}
    for invitation in invitations:
        if (
            invitation.workspace_id is None
            or invitation.status != InvitationStatus.PENDING
        ):
            continue
        key = (invitation.organization_id, invitation.email.lower())
        workspace_rows_by_key.setdefault(key, []).append(invitation)

    groups: list[InvitationGroup] = []
    handled_pending_workspace_ids: set[InvitationID] = set()
    for invitation in invitations:
        key = (invitation.organization_id, invitation.email.lower())
        if (
            invitation.workspace_id is None
            and invitation.status == InvitationStatus.PENDING
        ):
            workspace_invitations = workspace_rows_by_key.get(key, [])
            handled_pending_workspace_ids.update(
                workspace_invitation.id
                for workspace_invitation in workspace_invitations
            )
            groups.append(
                InvitationGroup(
                    invitation=invitation,
                    workspace_invitations=workspace_invitations,
                    accept_token=invitation.token,
                )
            )
            continue

        if (
            invitation.workspace_id is not None
            and invitation.status == InvitationStatus.PENDING
            and key in pending_orgs_by_key
            and invitation.id in handled_pending_workspace_ids
        ):
            continue

        groups.append(
            InvitationGroup(
                invitation=invitation,
                workspace_invitations=[],
                accept_token=invitation.token,
            )
        )
    return groups


async def get_invitation_group_by_token(
    session: AsyncSession,
    *,
    token: str,
) -> InvitationGroup:
    """Resolve a token to its grouped org or standalone workspace invitation view."""
    return await _resolve_group_by_token(session, token=token)


async def list_pending_invitation_groups_for_email(
    session: AsyncSession,
    *,
    email: str,
) -> list[InvitationGroup]:
    """List pending, unexpired invitation groups for an email address."""
    normalized_email = email.strip().lower()
    if not normalized_email:
        return []

    result = await session.execute(
        select(Invitation)
        .where(
            func.lower(Invitation.email) == normalized_email,
            Invitation.status == InvitationStatus.PENDING,
            Invitation.expires_at > datetime.now(UTC),
        )
        .options(
            selectinload(Invitation.organization),
            selectinload(Invitation.inviter),
            selectinload(Invitation.role_obj),
            selectinload(Invitation.workspace),
        )
        .order_by(Invitation.created_at.desc())
    )
    return _group_invitations(result.scalars().all())


async def _decline_pending_rows(
    session: AsyncSession,
    *,
    invitations: Sequence[Invitation],
) -> None:
    for invitation in invitations:
        if invitation.status == InvitationStatus.PENDING:
            invitation.status = InvitationStatus.DECLINED
            invitation.accepted_at = None


async def _accept_workspace_invitation(
    session: AsyncSession,
    *,
    invitation: Invitation,
    user_id: UserID,
) -> Membership:
    organization_id = invitation.organization_id
    assert invitation.workspace_id is not None
    await _ensure_org_membership(
        session,
        organization_id=organization_id,
        user_id=user_id,
    )
    membership = await _ensure_workspace_membership(
        session,
        workspace_id=invitation.workspace_id,
        user_id=user_id,
    )
    await _upsert_user_role_assignment(
        session,
        organization_id=organization_id,
        user_id=user_id,
        workspace_id=invitation.workspace_id,
        role_id=invitation.role_id,
        replace_existing=True,
    )
    return membership


async def _accept_org_group_for_user(
    session: AsyncSession,
    *,
    group: InvitationGroup,
    user_id: UserID,
    selected_workspace_ids: list[WorkspaceID] | None,
) -> OrganizationMembership:
    invitation = group.invitation
    selected_ids = set(selected_workspace_ids or [])
    if group.workspace_invitations and not selected_ids:
        raise TracecatValidationError(
            "Select at least one workspace before accepting this invitation"
        )

    membership = await _ensure_org_membership(
        session,
        organization_id=invitation.organization_id,
        user_id=user_id,
    )
    await _upsert_user_role_assignment(
        session,
        organization_id=invitation.organization_id,
        user_id=user_id,
        workspace_id=None,
        role_id=invitation.role_id,
        replace_existing=True,
    )

    now = datetime.now(UTC)
    invitation.status = InvitationStatus.ACCEPTED
    invitation.accepted_at = now

    for workspace_invitation in group.workspace_invitations:
        if workspace_invitation.workspace_id in selected_ids:
            workspace_invitation.status = InvitationStatus.ACCEPTED
            workspace_invitation.accepted_at = now
            await _accept_workspace_invitation(
                session,
                invitation=workspace_invitation,
                user_id=user_id,
            )
        else:
            workspace_invitation.status = InvitationStatus.DECLINED
            workspace_invitation.accepted_at = None

    return membership


async def accept_invitation_for_user(
    session: AsyncSession,
    *,
    user_id: UserID,
    token: str,
    selected_workspace_ids: list[WorkspaceID] | None = None,
) -> OrganizationMembership | Membership:
    """Accept a unified invitation for an authenticated user."""
    group = await _resolve_group_by_token(session, token=token)
    invitation = group.invitation
    user = await _load_user_by_id(session, user_id=user_id)
    if user.email.lower() != invitation.email.lower():
        raise TracecatAuthorizationError(
            "This invitation was sent to a different email address"
        )
    if invitation.expires_at < datetime.now(UTC):
        raise TracecatAuthorizationError("Invitation has expired")
    if invitation.status != InvitationStatus.PENDING:
        raise TracecatAuthorizationError(
            f"Invitation is not pending: {invitation.status}"
        )

    resource_type = (
        "workspace_invitation"
        if invitation.workspace_id is not None
        else "organization_invitation"
    )
    audit_role = Role(
        type="user",
        user_id=user_id,
        organization_id=invitation.organization_id,
        service_id="tracecat-api",
    )

    async with AuditService.with_session(audit_role, session=session) as svc:
        await svc.create_event(
            resource_type=resource_type,
            action="accept",
            resource_id=invitation.id,
            status=AuditEventStatus.ATTEMPT,
        )

    try:
        if invitation.workspace_id is None:
            membership = await _accept_org_group_for_user(
                session,
                group=group,
                user_id=user_id,
                selected_workspace_ids=selected_workspace_ids,
            )
        else:
            invitation.status = InvitationStatus.ACCEPTED
            invitation.accepted_at = datetime.now(UTC)
            membership = await _accept_workspace_invitation(
                session,
                invitation=invitation,
                user_id=user_id,
            )

        await session.commit()
        invalidate_authz_caches()
        await session.refresh(membership)
    except Exception:
        async with AuditService.with_session(audit_role, session=session) as svc:
            await svc.create_event(
                resource_type=resource_type,
                action="accept",
                resource_id=invitation.id,
                status=AuditEventStatus.FAILURE,
            )
        raise

    async with AuditService.with_session(audit_role, session=session) as svc:
        await svc.create_event(
            resource_type=resource_type,
            action="accept",
            resource_id=invitation.id,
            status=AuditEventStatus.SUCCESS,
        )
    return membership


async def decline_invitation_for_user(
    session: AsyncSession,
    *,
    user_id: UserID,
    token: str,
) -> None:
    """Decline a unified invitation for an authenticated user."""
    group = await _resolve_group_by_token(session, token=token)
    invitation = group.invitation
    user = await _load_user_by_id(session, user_id=user_id)
    if user.email.lower() != invitation.email.lower():
        raise TracecatAuthorizationError(
            "This invitation was sent to a different email address"
        )
    if invitation.status != InvitationStatus.PENDING:
        raise TracecatAuthorizationError(
            f"Invitation is not pending: {invitation.status}"
        )

    rows = (
        [invitation, *group.workspace_invitations]
        if invitation.workspace_id is None
        else [invitation]
    )
    await _decline_pending_rows(session, invitations=rows)
    await session.commit()


class InvitationService(BaseOrgService):
    """Consolidated invitation service for org and workspace flows."""

    service_name = "invitation"

    async def _get_workspace_organization_id(
        self, workspace_id: WorkspaceID
    ) -> uuid.UUID:
        result = await self.session.execute(
            select(Workspace.organization_id).where(Workspace.id == workspace_id)
        )
        ws_org_id = result.scalar_one_or_none()
        if ws_org_id is None:
            raise TracecatValidationError("Workspace not found")
        if ws_org_id != self.organization_id:
            raise TracecatAuthorizationError(
                "Workspace does not belong to this organization"
            )
        return ws_org_id

    async def _get_existing_org_member_by_email(
        self,
        *,
        organization_id: uuid.UUID,
        email: str,
    ) -> User | None:
        result = await self.session.execute(
            select(User)
            .join(
                OrganizationMembership,
                OrganizationMembership.user_id == cast(User.id, UUID),
            )
            .where(
                OrganizationMembership.organization_id == organization_id,
                func.lower(User.email) == email.lower(),
            )
        )
        return result.scalar_one_or_none()

    async def _create_workspace_rows_for_org_invitation(
        self,
        *,
        email: str,
        workspace_assignments: list[tuple[WorkspaceID, uuid.UUID]],
        expires_at: datetime,
    ) -> None:
        if self.role is None or self.role.user_id is None:
            raise TracecatAuthorizationError(
                "User must be authenticated to create invitation"
            )

        workspace_ids = {workspace_id for workspace_id, _ in workspace_assignments}
        ws_role_ids = {role_id for _, role_id in workspace_assignments}

        ws_result = await self.session.execute(
            select(Workspace.id, Workspace.organization_id, Workspace.name).where(
                Workspace.id.in_(workspace_ids)
            )
        )
        workspace_rows = ws_result.tuples().all()
        workspace_orgs = {
            workspace_id: org_id for workspace_id, org_id, _ in workspace_rows
        }
        if missing_workspace_ids := workspace_ids - set(workspace_orgs):
            raise TracecatValidationError(
                f"Invalid workspace IDs: {sorted(str(ws_id) for ws_id in missing_workspace_ids)}"
            )
        if any(org_id != self.organization_id for org_id in workspace_orgs.values()):
            raise TracecatAuthorizationError(
                "One or more workspaces do not belong to this organization"
            )

        role_result = await self.session.execute(
            select(DBRole.id, DBRole.slug).where(
                DBRole.id.in_(ws_role_ids),
                DBRole.organization_id == self.organization_id,
            )
        )
        role_map = dict(role_result.tuples().all())
        if missing_role_ids := ws_role_ids - set(role_map):
            raise TracecatValidationError(
                f"Invalid role IDs: {sorted(str(role_id) for role_id in missing_role_ids)}"
            )

        for slug in role_map.values():
            if slug is not None and slug.startswith("organization-"):
                raise TracecatValidationError(
                    "Workspace assignment role must be a workspace role"
                )

        for workspace_id, role_id in workspace_assignments:
            ws_scopes = await _compute_workspace_effective_scopes(
                self.session,
                role=self.role,
                workspace_id=workspace_id,
            )
            if not has_scope(ws_scopes, "workspace:member:invite"):
                raise TracecatAuthorizationError(
                    "Insufficient permissions to invite members for one or more workspaces"
                )

            await _create_invitation_row(
                self.session,
                organization_id=self.organization_id,
                email=email,
                role_id=role_id,
                workspace_id=workspace_id,
                invited_by=self.role.user_id,
                expires_at=expires_at,
            )

    async def create_invitation(
        self,
        params: InvitationCreate,
    ) -> Invitation | None:
        """Create a unified invitation or direct-add existing org members."""
        if self.role is None or self.role.user_id is None:
            raise TracecatAuthorizationError(
                "User must be authenticated to create invitation"
            )

        email = params.email.lower()
        expires_at = datetime.now(UTC) + timedelta(days=7)

        if params.workspace_id is not None:
            organization_id = await self._get_workspace_organization_id(
                params.workspace_id
            )
            await _resolve_role_for_scope(
                self.session,
                organization_id=organization_id,
                role_id=params.role_id,
                workspace_id=params.workspace_id,
            )
            existing_user = await self._get_existing_org_member_by_email(
                organization_id=organization_id,
                email=email,
            )
            if existing_user is not None:
                await _ensure_workspace_membership(
                    self.session,
                    workspace_id=params.workspace_id,
                    user_id=existing_user.id,
                )
                await _upsert_user_role_assignment(
                    self.session,
                    organization_id=organization_id,
                    user_id=existing_user.id,
                    workspace_id=params.workspace_id,
                    role_id=params.role_id,
                    replace_existing=True,
                )
                await self.session.commit()
                invalidate_authz_caches()
                return None

            invitation = await _create_invitation_row(
                self.session,
                organization_id=organization_id,
                email=email,
                role_id=params.role_id,
                workspace_id=params.workspace_id,
                invited_by=self.role.user_id,
                expires_at=expires_at,
            )
            await self.session.commit()
            result = await self.session.execute(
                select(Invitation)
                .where(Invitation.id == invitation.id)
                .options(
                    selectinload(Invitation.role_obj),
                    selectinload(Invitation.workspace),
                )
            )
            return result.scalar_one()

        await _resolve_role_for_scope(
            self.session,
            organization_id=self.organization_id,
            role_id=params.role_id,
            workspace_id=None,
        )
        if (
            existing_user := await self._get_existing_org_member_by_email(
                organization_id=self.organization_id,
                email=email,
            )
        ) is not None:
            raise TracecatValidationError(
                f"{existing_user.email} is already a member of this organization"
            )

        invitation = await _create_invitation_row(
            self.session,
            organization_id=self.organization_id,
            email=email,
            role_id=params.role_id,
            workspace_id=None,
            invited_by=self.role.user_id,
            expires_at=expires_at,
        )
        if params.workspace_assignments:
            await self._create_workspace_rows_for_org_invitation(
                email=email,
                workspace_assignments=[
                    (assignment.workspace_id, assignment.role_id)
                    for assignment in params.workspace_assignments
                ],
                expires_at=expires_at,
            )
        await self.session.commit()
        result = await self.session.execute(
            select(Invitation)
            .where(Invitation.id == invitation.id)
            .options(
                selectinload(Invitation.organization),
                selectinload(Invitation.inviter),
                selectinload(Invitation.role_obj),
            )
        )
        return result.scalar_one()

    async def create_org_invitation(
        self,
        *,
        email: str,
        role_id: uuid.UUID,
        workspace_assignments: list[tuple[WorkspaceID, uuid.UUID]] | None = None,
    ) -> Invitation:
        """Compatibility wrapper for stale org-scoped callers."""
        invitation_params = InvitationCreate.model_validate(
            {
                "email": email,
                "role_id": role_id,
                "workspace_assignments": [
                    {
                        "workspace_id": workspace_id,
                        "role_id": assignment_role_id,
                    }
                    for workspace_id, assignment_role_id in workspace_assignments or []
                ]
                or None,
            }
        )
        invitation = await self.create_invitation(
            invitation_params,
        )
        if invitation is None:
            raise TracecatValidationError(
                f"{email} is already a member of this organization"
            )
        return invitation

    async def create_workspace_invitation(
        self,
        workspace_id: WorkspaceID,
        params: InvitationCreate,
    ) -> Invitation | None:
        """Compatibility wrapper for stale workspace-scoped callers."""
        return await self.create_invitation(
            params.model_copy(update={"workspace_id": workspace_id})
        )

    async def list_invitations(
        self,
        *,
        workspace_id: WorkspaceID | None = None,
        status: InvitationStatus | None = None,
    ) -> Sequence[Invitation]:
        """List raw invitation rows in this organization."""
        statement = select(Invitation).where(
            Invitation.organization_id == self.organization_id
        )
        if workspace_id is not None:
            await self._get_workspace_organization_id(workspace_id)
            statement = statement.where(Invitation.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(Invitation.status == status)
        statement = statement.options(
            selectinload(Invitation.role_obj),
            selectinload(Invitation.workspace),
        ).order_by(Invitation.created_at.desc())
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def list_grouped_invitations(
        self,
        *,
        workspace_id: WorkspaceID | None = None,
        status: InvitationStatus | None = None,
    ) -> Sequence[InvitationGroup]:
        """List invitations grouped by pending org invite intent."""
        invitations = await self.list_invitations(
            workspace_id=workspace_id,
            status=status,
        )
        return _group_invitations(invitations)

    async def list_org_invitations(
        self,
        *,
        status: InvitationStatus | None = None,
    ) -> Sequence[Invitation]:
        """Compatibility wrapper for stale org-scoped callers."""
        return await self.list_invitations(status=status)

    async def list_workspace_invitations(
        self,
        workspace_id: WorkspaceID,
        *,
        status: InvitationStatus | None = None,
    ) -> Sequence[Invitation]:
        """Compatibility wrapper for stale workspace-scoped callers."""
        return await self.list_invitations(workspace_id=workspace_id, status=status)

    async def get_invitation(self, invitation_id: InvitationID) -> Invitation:
        """Get an invitation row within this organization."""
        result = await self.session.execute(
            select(Invitation)
            .where(
                Invitation.id == invitation_id,
                Invitation.organization_id == self.organization_id,
            )
            .options(
                selectinload(Invitation.organization),
                selectinload(Invitation.inviter),
                selectinload(Invitation.role_obj),
                selectinload(Invitation.workspace),
            )
        )
        invitation = result.scalar_one_or_none()
        if invitation is None:
            raise TracecatNotFoundError("Invitation not found")
        return invitation

    async def revoke_invitation(self, invitation_id: InvitationID) -> Invitation:
        """Revoke a pending invitation row, cascading org pending groups."""
        invitation = await self.get_invitation(invitation_id)
        if invitation.status != InvitationStatus.PENDING:
            raise TracecatValidationError(
                f"Cannot revoke invitation with status '{invitation.status}'"
            )

        invitation.status = InvitationStatus.REVOKED
        invitation.accepted_at = None
        if invitation.workspace_id is None:
            rows = await _load_pending_workspace_options(
                self.session,
                organization_id=invitation.organization_id,
                email=invitation.email,
            )
            for row in rows:
                row.status = InvitationStatus.REVOKED
                row.accepted_at = None

        await self.session.commit()
        await self.session.refresh(invitation)
        return invitation

    async def get_invitation_group_by_token(self, token: str) -> InvitationGroup:
        """Resolve a token to a grouped invitation view within this service session."""
        return await get_invitation_group_by_token(self.session, token=token)

    async def list_pending_invitation_groups_for_email(
        self,
        *,
        email: str,
    ) -> list[InvitationGroup]:
        """List grouped pending invitations for the invitee email."""
        return await list_pending_invitation_groups_for_email(
            self.session,
            email=email,
        )

    async def build_grouped_invitation(
        self,
        invitation: Invitation,
    ) -> InvitationGroup:
        """Build grouped view for an invitation row."""
        if (
            invitation.workspace_id is None
            and invitation.status == InvitationStatus.PENDING
        ):
            return await _build_group_from_org_invitation(
                self.session,
                invitation=invitation,
            )
        return InvitationGroup(
            invitation=invitation,
            workspace_invitations=[],
            accept_token=invitation.token,
        )
