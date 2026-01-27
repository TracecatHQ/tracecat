from __future__ import annotations

import secrets
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from pydantic import UUID4
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import load_only, noload, selectinload

from tracecat.audit.logger import audit_log
from tracecat.auth.types import AccessLevel, Role
from tracecat.authz.controls import require_access_level, require_workspace_role
from tracecat.authz.enums import OrgRole, OwnerType, WorkspaceRole
from tracecat.cases.service import CaseFieldsService
from tracecat.db.models import (
    Invitation,
    Membership,
    OrganizationMembership,
    Ownership,
    User,
    Workspace,
)
from tracecat.exceptions import (
    TracecatException,
    TracecatManagementError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import InvitationID, UserID, WorkspaceID
from tracecat.invitations.enums import InvitationStatus
from tracecat.service import BaseOrgService
from tracecat.workflow.schedules.service import WorkflowSchedulesService
from tracecat.workspaces.schemas import (
    WorkspaceInvitationCreate,
    WorkspaceSearch,
    WorkspaceUpdate,
)


class WorkspaceService(BaseOrgService):
    """Manage workspaces."""

    service_name = "workspace"
    _load_only = ["id", "name"]

    @require_access_level(AccessLevel.ADMIN)
    async def admin_list_workspaces(
        self, limit: int | None = None
    ) -> Sequence[Workspace]:
        """List all workspaces in the organization."""
        statement = (
            select(Workspace)
            .options(
                load_only(
                    *(getattr(Workspace, f) for f in self._load_only)
                ),  # only what the route returns
                noload("*"),  # disable all relationship loaders
            )
            .where(Workspace.organization_id == self.organization_id)
        )
        if limit is not None:
            if limit <= 0:
                raise TracecatException("List workspace limit must be greater than 0")
            statement = statement.limit(limit)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def list_workspaces(
        self, user_id: UserID, limit: int | None = None
    ) -> Sequence[Workspace]:
        """List all workspaces that a user is a member of."""
        statement = (
            select(Workspace)
            .where(
                Workspace.id == Membership.workspace_id, Membership.user_id == user_id
            )
            .options(
                load_only(*(getattr(Workspace, f) for f in self._load_only)),
                noload("*"),
            )
        )
        if limit is not None:
            if limit <= 0:
                raise TracecatException("List workspace limit must be greater than 0")
            statement = statement.limit(limit)
        result = await self.session.execute(statement)
        return result.scalars().all()

    @audit_log(resource_type="workspace", action="create")
    @require_access_level(AccessLevel.ADMIN)
    async def create_workspace(
        self,
        name: str,
        *,
        override_id: UUID4 | None = None,
        users: list[User] | None = None,
    ) -> Workspace:
        """Create a new workspace."""
        kwargs = {
            "name": name,
            "organization_id": self.organization_id,
            # Workspace model defines the relationship as "members"
            "members": users or [],
        }
        if override_id:
            kwargs["id"] = override_id
        workspace = Workspace(**kwargs)
        self.session.add(workspace)
        await self.session.flush()

        # Create ownership record
        ownership = Ownership(
            resource_id=str(workspace.id),
            resource_type="workspace",
            owner_id=self.organization_id,
            owner_type=OwnerType.USER.value,
        )
        self.session.add(ownership)

        # Initialize workspace-scoped case fields schema in the same transaction
        bootstrap_role = Role(
            type="service",
            service_id="tracecat-service",
            organization_id=self.organization_id,
            workspace_id=workspace.id,
            workspace_role=WorkspaceRole.ADMIN,
            access_level=AccessLevel.ADMIN,
        )
        case_fields_service = CaseFieldsService(
            session=self.session, role=bootstrap_role
        )
        await case_fields_service.initialize_workspace_schema()

        await self.session.commit()
        await self.session.refresh(workspace)

        return workspace

    async def get_workspace(self, workspace_id: WorkspaceID) -> Workspace | None:
        """Retrieve a workspace by ID."""
        statement = select(Workspace).where(
            Workspace.organization_id == self.organization_id,
            Workspace.id == workspace_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    @audit_log(resource_type="workspace", action="update")
    @require_access_level(AccessLevel.ADMIN)
    async def update_workspace(
        self, workspace: Workspace, params: WorkspaceUpdate
    ) -> Workspace:
        """Update a workspace."""
        set_fields = params.model_dump(exclude_unset=True)
        self.logger.info("Updating workspace", set_fields=set_fields)
        for field, value in set_fields.items():
            setattr(workspace, field, value)
        self.session.add(workspace)
        await self.session.commit()
        await self.session.refresh(workspace)
        return workspace

    @audit_log(resource_type="workspace", action="delete")
    @require_access_level(AccessLevel.ADMIN)
    async def delete_workspace(self, workspace_id: WorkspaceID) -> None:
        """Delete a workspace."""
        all_workspaces = await self.admin_list_workspaces()
        if len(all_workspaces) == 1:
            raise TracecatManagementError(
                "There must be at least one workspace in the organization."
            )
        statement = select(Workspace).where(
            Workspace.organization_id == self.organization_id,
            Workspace.id == workspace_id,
        )
        result = await self.session.execute(statement)
        workspace = result.scalar_one()
        bootstrap_role = Role(
            type="service",
            service_id="tracecat-service",
            organization_id=self.organization_id,
            workspace_id=workspace.id,
            workspace_role=WorkspaceRole.ADMIN,
            access_level=AccessLevel.ADMIN,
        )

        # Delete Temporal schedules before workspace deletion
        schedule_service = WorkflowSchedulesService(
            session=self.session, role=bootstrap_role
        )
        for schedule in await schedule_service.list_schedules():
            await schedule_service.delete_schedule(schedule.id, commit=False)

        case_fields_service = CaseFieldsService(
            session=self.session, role=bootstrap_role
        )
        await case_fields_service.drop_workspace_schema()
        await self.session.delete(workspace)
        await self.session.commit()

    async def search_workspaces(self, params: WorkspaceSearch) -> Sequence[Workspace]:
        """Retrieve a workspace by ID."""
        statement = select(Workspace).where(
            Workspace.organization_id == self.organization_id
        )
        if self.role is not None and self.role.access_level < AccessLevel.ADMIN:
            # Only list workspaces where user is a member
            statement = statement.where(
                Workspace.id == Membership.workspace_id,
                Membership.user_id == self.role.user_id,
            )
        if params.name:
            statement = statement.where(Workspace.name == params.name)
        result = await self.session.execute(statement)
        return result.scalars().all()

    # === Invitation Management === #

    @staticmethod
    def _generate_invitation_token() -> str:
        """Generate a unique 64-character token for invitation magic links."""
        return secrets.token_urlsafe(48)[:64]

    @audit_log(resource_type="workspace_invitation", action="create")
    @require_workspace_role(WorkspaceRole.ADMIN)
    async def create_invitation(
        self,
        workspace_id: WorkspaceID,
        params: WorkspaceInvitationCreate,
    ) -> Invitation:
        """Create a new workspace invitation.

        Args:
            workspace_id: The workspace to invite the user to.
            params: The invitation parameters (email, role).

        Returns:
            The created invitation.

        Raises:
            TracecatValidationError: If there is already a pending invitation
                for this email in this workspace.
        """
        # Check for existing pending invitation that hasn't expired
        now = datetime.now(UTC)
        existing_stmt = select(Invitation).where(
            Invitation.workspace_id == workspace_id,
            Invitation.email == params.email,
            Invitation.status == InvitationStatus.PENDING,
            Invitation.expires_at > now,
        )
        existing = await self.session.execute(existing_stmt)
        if existing.scalar_one_or_none():
            raise TracecatValidationError(
                f"A pending invitation already exists for {params.email}"
            )

        # Default expiry: 7 days
        expires_at = datetime.now(UTC) + timedelta(days=7)

        invitation = Invitation(
            workspace_id=workspace_id,
            email=params.email,
            role=params.role,
            status=InvitationStatus.PENDING,
            invited_by=self.role.user_id if self.role else None,
            token=self._generate_invitation_token(),
            expires_at=expires_at,
        )
        self.session.add(invitation)

        try:
            await self.session.flush()
        except IntegrityError as e:
            await self.session.rollback()
            if "uq_invitation_workspace_id_email" in str(e):
                # Check if the existing invitation is expired - if so, delete and retry
                existing_stmt = select(Invitation).where(
                    Invitation.workspace_id == workspace_id,
                    Invitation.email == params.email,
                )
                result = await self.session.execute(existing_stmt)
                existing_invitation = result.scalar_one_or_none()

                if existing_invitation and existing_invitation.expires_at < now:
                    # Delete the expired invitation and retry
                    await self.session.delete(existing_invitation)
                    await self.session.commit()
                    # Recurse to create the new invitation
                    return await self.create_invitation(workspace_id, params)

                raise TracecatValidationError(
                    f"An invitation already exists for {params.email} in this workspace"
                ) from e
            raise

        await self.session.commit()
        await self.session.refresh(invitation)
        return invitation

    @require_workspace_role(WorkspaceRole.ADMIN)
    async def list_invitations(
        self,
        workspace_id: WorkspaceID,
        status: InvitationStatus | None = None,
    ) -> Sequence[Invitation]:
        """List invitations for a workspace.

        Args:
            workspace_id: The workspace to list invitations for.
            status: Optional filter by invitation status.

        Returns:
            List of invitations matching the criteria.
        """
        statement = select(Invitation).where(Invitation.workspace_id == workspace_id)
        if status is not None:
            statement = statement.where(Invitation.status == status)
        statement = statement.order_by(Invitation.created_at.desc())
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_invitation_by_token(self, token: str) -> Invitation | None:
        """Retrieve an invitation by its unique token.

        This method does not require workspace role checks as it's used
        during the public invitation acceptance flow.

        Args:
            token: The invitation token.

        Returns:
            The invitation if found, None otherwise.
        """
        statement = (
            select(Invitation)
            .where(Invitation.token == token)
            .options(selectinload(Invitation.workspace))
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    @audit_log(resource_type="workspace_invitation", action="accept")
    async def accept_invitation(
        self,
        token: str,
        user_id: UserID,
    ) -> Membership:
        """Accept a workspace invitation.

        This method validates the invitation, creates org membership if needed,
        creates the workspace membership, and updates the invitation status.

        Uses optimistic locking via conditional UPDATE to prevent TOCTOU race
        conditions - the status check and update happen atomically in a single
        database operation.

        Note: This method does not require workspace role checks as it's called
        by the user accepting their own invitation.

        Args:
            token: The invitation token.
            user_id: The user accepting the invitation.

        Returns:
            The created workspace membership.

        Raises:
            TracecatNotFoundError: If the invitation is not found.
            TracecatValidationError: If the invitation is expired, already
                accepted, or revoked.
        """
        invitation = await self.get_invitation_by_token(token)
        if invitation is None:
            raise TracecatNotFoundError("Invitation not found")

        # Check expiry before attempting atomic update
        if invitation.expires_at < datetime.now(UTC):
            raise TracecatValidationError("Invitation has expired")

        # Get the workspace to find the organization
        workspace = invitation.workspace
        organization_id = workspace.organization_id

        # Check if user is already a member of the organization
        org_membership_stmt = select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.organization_id == organization_id,
        )
        result = await self.session.execute(org_membership_stmt)
        org_membership = result.scalar_one_or_none()

        # If not in org, auto-create as MEMBER
        if org_membership is None:
            org_membership = OrganizationMembership(
                user_id=user_id,
                organization_id=organization_id,
                role=OrgRole.MEMBER,
            )
            self.session.add(org_membership)
            await self.session.flush()

        # Check if user is already a member of the workspace
        ws_membership_stmt = select(Membership).where(
            Membership.user_id == user_id,
            Membership.workspace_id == invitation.workspace_id,
        )
        result = await self.session.execute(ws_membership_stmt)
        if result.scalar_one_or_none():
            raise TracecatValidationError("User is already a member of this workspace")

        # Atomically update invitation status only if still PENDING.
        # This prevents TOCTOU race conditions where an admin might revoke
        # the invitation between our check and commit.
        now = datetime.now(UTC)
        update_result = await self.session.execute(
            update(Invitation)
            .where(
                Invitation.id == invitation.id,
                Invitation.status == InvitationStatus.PENDING,
            )
            .values(status=InvitationStatus.ACCEPTED, accepted_at=now)
        )

        if update_result.rowcount == 0:  # pyright: ignore[reportAttributeAccessIssue]
            # Status changed between fetch and update - re-fetch for accurate error
            await self.session.refresh(invitation)
            if invitation.status == InvitationStatus.ACCEPTED:
                raise TracecatValidationError("Invitation has already been accepted")
            if invitation.status == InvitationStatus.REVOKED:
                raise TracecatValidationError("Invitation has been revoked")
            # Shouldn't reach here, but handle gracefully
            raise TracecatValidationError("Invitation is no longer valid")

        # Create workspace membership
        membership = Membership(
            user_id=user_id,
            workspace_id=invitation.workspace_id,
            role=invitation.role,
        )
        self.session.add(membership)

        await self.session.commit()
        await self.session.refresh(membership)
        return membership

    @audit_log(resource_type="workspace_invitation", action="revoke")
    @require_workspace_role(WorkspaceRole.ADMIN)
    async def revoke_invitation(
        self,
        workspace_id: WorkspaceID,
        invitation_id: InvitationID,
    ) -> None:
        """Revoke a pending workspace invitation.

        Args:
            workspace_id: The workspace the invitation belongs to.
            invitation_id: The invitation to revoke.

        Raises:
            TracecatNotFoundError: If the invitation is not found.
            TracecatValidationError: If the invitation is not pending.
        """
        statement = select(Invitation).where(
            Invitation.id == invitation_id,
            Invitation.workspace_id == workspace_id,
        )
        result = await self.session.execute(statement)
        invitation = result.scalar_one_or_none()

        if invitation is None:
            raise TracecatNotFoundError("Invitation not found")

        if invitation.status != InvitationStatus.PENDING:
            raise TracecatValidationError(
                f"Cannot revoke invitation with status '{invitation.status}'"
            )

        invitation.status = InvitationStatus.REVOKED
        await self.session.commit()
