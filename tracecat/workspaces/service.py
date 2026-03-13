from __future__ import annotations

import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import UUID4
from sqlalchemy import bindparam, cast, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import load_only, noload

from tracecat.audit.logger import audit_log
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.authz.enums import OwnerType
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.cases.service import CaseFieldsService
from tracecat.db.models import (
    Invitation,
    Membership,
    Ownership,
    User,
    Workspace,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatException,
    TracecatManagementError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import InvitationID, UserID, WorkspaceID
from tracecat.invitations.enums import InvitationStatus
from tracecat.invitations.schemas import InvitationCreate
from tracecat.invitations.service import (
    InvitationService,
)
from tracecat.invitations.service import (
    accept_invitation_for_user as accept_unified_invitation_for_user,
)
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

    async def admin_list_workspaces(
        self, limit: int | None = None
    ) -> Sequence[Workspace]:
        """List all workspaces in the organization.

        Note: Authorization is handled at the router level via scope checks.
        """
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

    @require_scope("workspace:create")
    @audit_log(resource_type="workspace", action="create")
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
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
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

    @require_scope("workspace:update")
    @audit_log(resource_type="workspace", action="update")
    async def update_workspace(
        self, workspace: Workspace, params: WorkspaceUpdate
    ) -> Workspace:
        """Update a workspace."""
        set_fields = params.model_dump(exclude_unset=True)
        self.logger.info("Updating workspace", set_fields=set_fields)
        missing = object()
        settings_update = set_fields.pop("settings", missing)
        if settings_update is missing and not set_fields:
            return workspace

        statement = update(Workspace).where(
            Workspace.organization_id == self.organization_id,
            Workspace.id == workspace.id,
        )

        statement_params: dict[str, object] = {}
        if settings_update is not missing:
            if settings_update is None:
                statement = statement.values(settings={})
            else:
                statement = statement.values(
                    settings=func.coalesce(Workspace.settings, cast("{}", JSONB)).op(
                        "||"
                    )(bindparam("settings_patch", type_=JSONB))
                )
                statement_params["settings_patch"] = settings_update
        if set_fields:
            statement = statement.values(**set_fields)

        await self.session.execute(statement, statement_params)
        await self.session.commit()
        await self.session.refresh(workspace)
        return workspace

    @require_scope("workspace:delete")
    @audit_log(resource_type="workspace", action="delete")
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
            scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
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
        # Platform admins and org owners/admins can see all workspaces
        if self.role is not None and not self.role.is_privileged:
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

    @require_scope("workspace:member:invite")
    @audit_log(resource_type="workspace_invitation", action="create")
    async def create_invitation(
        self,
        workspace_id: WorkspaceID,
        params: WorkspaceInvitationCreate,
    ) -> Invitation | None:
        """Create a new workspace invitation.

        Args:
            workspace_id: The workspace to invite the user to.
            params: The invitation parameters (email, role_id).

        Returns:
            The created invitation, or `None` when an existing organization
            member was added to the workspace directly.

        Raises:
            TracecatValidationError: If there is already a pending invitation
                for this email in this workspace.
        """

        try:
            role_id = uuid.UUID(params.role_id)
        except ValueError as e:
            raise TracecatValidationError("Invalid role ID format") from e

        service = InvitationService(self.session, role=self.role)
        invitation = await service.create_workspace_invitation(
            workspace_id,
            InvitationCreate(
                email=params.email,
                role_id=role_id,
                workspace_id=workspace_id,
            ),
        )
        return invitation

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
        service = InvitationService(self.session, role=self.role)
        return await service.list_workspace_invitations(
            workspace_id,
            status=status,
        )

    async def get_invitation_by_token(self, token: str) -> Invitation | None:
        """Retrieve an invitation by its unique token.

        This method does not require workspace role checks as it's used
        during the public invitation acceptance flow.

        Args:
            token: The invitation token.

        Returns:
            The invitation if found, None otherwise.
        """
        statement = select(Invitation).where(Invitation.token == token)
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
        if invitation.status == InvitationStatus.ACCEPTED:
            raise TracecatValidationError("Invitation has already been accepted")
        if invitation.status == InvitationStatus.REVOKED:
            raise TracecatValidationError("Invitation has been revoked")
        if invitation.expires_at < datetime.now(UTC):
            raise TracecatValidationError("Invitation has expired")
        existing_membership = await self.session.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.workspace_id == invitation.workspace_id,
            )
        )
        if existing_membership.scalar_one_or_none() is not None:
            raise TracecatValidationError("User is already a member of this workspace")

        try:
            membership = await accept_unified_invitation_for_user(
                self.session,
                user_id=user_id,
                token=token,
            )
        except TracecatAuthorizationError as e:
            raise TracecatValidationError(str(e)) from e

        if not isinstance(membership, Membership):
            raise TracecatValidationError(
                "Invitation token does not target a workspace"
            )
        return membership

    @require_scope("workspace:member:remove")
    @audit_log(resource_type="workspace_invitation", action="revoke")
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
        service = InvitationService(self.session, role=self.role)
        invitation = await service.get_invitation(invitation_id)
        if invitation.workspace_id != workspace_id:
            raise TracecatNotFoundError("Invitation not found")
        await service.revoke_invitation(invitation_id)
