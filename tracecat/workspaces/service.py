from __future__ import annotations

import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from pydantic import UUID4
from sqlalchemy import bindparam, cast, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, noload, selectinload

from tracecat.audit.logger import audit_log
from tracecat.auth.types import Role
from tracecat.authz.controls import (
    check_no_role_escalation,
    has_scope,
    require_scope,
)
from tracecat.authz.enums import OwnerType
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.authz.service import MembershipService
from tracecat.cases.service import CaseFieldsService
from tracecat.db.models import (
    Invitation,
    Membership,
    OrganizationMembership,
    Ownership,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import (
    Role as DBRole,
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
from tracecat.invitations.service import batch_upsert_invitations
from tracecat.invitations.types import BatchInviteItem
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

    async def list_accessible_workspaces(
        self, limit: int | None = None
    ) -> Sequence[Workspace]:
        """List workspaces visible to the current actor."""
        if self.role.scopes and has_scope(self.role.scopes, "org:workspace:read"):
            return await self.admin_list_workspaces(limit=limit)

        if self.role.type == "service_account":
            if (
                bound_workspace_id := self.role.bound_workspace_id
                or self.role.workspace_id
            ) is None:
                if self.role.scopes and has_scope(self.role.scopes, "org:read"):
                    return []
                raise TracecatAuthorizationError(
                    "Service account does not have access to list workspaces"
                )
            workspace = await self.get_workspace(bound_workspace_id)
            return [workspace] if workspace is not None else []

        if self.role.user_id is None:
            raise TracecatAuthorizationError("User ID is required to list workspaces")
        return await self.list_workspaces(self.role.user_id, limit=limit)

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
        """Search workspaces visible to the current actor."""
        statement = select(Workspace).where(
            Workspace.organization_id == self.organization_id
        )

        if self.role is not None and not self.role.is_privileged:
            if self.role.type == "service_account":
                if (
                    bound_workspace_id := self.role.bound_workspace_id
                    or self.role.workspace_id
                ) is None:
                    if self.role.scopes and has_scope(self.role.scopes, "org:read"):
                        return []
                    raise TracecatAuthorizationError(
                        "Service account does not have access to search workspaces"
                    )
                statement = statement.where(Workspace.id == bound_workspace_id)
            else:
                if self.role.user_id is None:
                    raise TracecatAuthorizationError(
                        "User ID is required to search workspaces"
                    )
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
    ) -> Invitation:
        """Create a new workspace invitation.

        Args:
            workspace_id: The workspace to invite the user to.
            params: The invitation parameters (email, role_id).

        Returns:
            The created invitation.

        Raises:
            TracecatValidationError: If there is already a pending invitation
                for this email in this workspace.
        """

        try:
            role_id = uuid.UUID(params.role_id)
        except ValueError as e:
            raise TracecatValidationError("Invalid role ID format") from e

        # Normalize so persisted emails are canonical (lowercased). The unique
        # constraint on (workspace_id, email) is case-sensitive, so storing only
        # the canonical form prevents cross-case duplicate invitations between
        # this path and the bulk upsert.
        email = params.email.strip().lower()

        # Validate role_id exists and belongs to this organization
        role_result = await self.session.execute(
            select(DBRole).where(
                DBRole.id == role_id,
                DBRole.organization_id == self.organization_id,
            )
        )
        role_obj = role_result.scalar_one_or_none()
        if role_obj is None:
            raise TracecatValidationError("Invalid role ID for this organization")

        # Prevent privilege escalation: cannot invite at a role granting more
        # access than the inviter holds.
        await check_no_role_escalation(self.session, self.role, role_id)

        # Check for existing pending invitation that hasn't expired
        now = datetime.now(UTC)
        existing_stmt = select(Invitation).where(
            Invitation.workspace_id == workspace_id,
            Invitation.email == email,
            Invitation.status == InvitationStatus.PENDING,
            Invitation.expires_at > now,
        )
        existing = await self.session.execute(existing_stmt)
        if existing.scalar_one_or_none():
            raise TracecatValidationError(
                f"A pending invitation already exists for {email}"
            )

        # Default expiry: 7 days
        expires_at = datetime.now(UTC) + timedelta(days=7)

        invitation = Invitation(
            workspace_id=workspace_id,
            email=email,
            role_id=role_id,
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
                    Invitation.email == email,
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
                    f"An invitation already exists for {email} in this workspace"
                ) from e
            raise

        await self.session.commit()
        result = await self.session.execute(
            select(Invitation)
            .where(Invitation.id == invitation.id)
            .options(selectinload(Invitation.role_obj))
        )
        return result.scalar_one()

    @require_scope("workspace:member:invite")
    async def batch_create_invitations(
        self,
        workspace_id: WorkspaceID,
        *,
        emails: list[str],
        role_id: str,
    ) -> list[BatchInviteItem]:
        """Create workspace invitations for many emails in one batched upsert.

        Emails are normalized (lowercased, stripped) and deduplicated. Existing
        workspace members are skipped. The upsert refreshes only stale
        invitations (revoked/accepted/expired); a live pending invitation is
        left untouched.

        Args:
            workspace_id: The workspace to invite users to.
            emails: Raw invitee emails (any case, possibly duplicated).
            role_id: RBAC role to assign upon acceptance.

        Returns:
            One :class:`BatchInviteItem` per distinct email, in input order.

        Raises:
            TracecatValidationError: If the role ID is invalid for this org.
        """
        if self.role is None:
            raise TracecatAuthorizationError("A role is required to create invitations")

        try:
            role_uuid = uuid.UUID(role_id)
        except ValueError as e:
            raise TracecatValidationError("Invalid role ID format") from e

        # Validate role belongs to this organization.
        role_result = await self.session.execute(
            select(DBRole).where(
                DBRole.id == role_uuid,
                DBRole.organization_id == self.organization_id,
            )
        )
        if role_result.scalar_one_or_none() is None:
            raise TracecatValidationError("Invalid role ID for this organization")

        # Prevent privilege escalation: cannot invite at a role granting more
        # access than the inviter holds. Applies to the whole request.
        await check_no_role_escalation(self.session, self.role, role_uuid)

        # Pre-filter existing workspace members (not covered by the
        # (workspace_id, email) unique constraint on invitations). The shared
        # implementation binds the normalized email set into this statement.
        normalized = list(dict.fromkeys(e.strip().lower() for e in emails if e.strip()))
        member_email_stmt = (
            select(func.lower(User.email))
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.workspace_id == workspace_id,
                func.lower(User.email).in_(normalized),
            )
        )
        return await batch_upsert_invitations(
            self.session,
            model=Invitation,
            emails=emails,
            role_id=role_uuid,
            invited_by=self.role.user_id,
            conflict_cols=["workspace_id", "email"],
            scope_filter=Invitation.workspace_id == workspace_id,
            member_email_stmt=member_email_stmt,
            member_skip_reason="Already a member of this workspace",
            extra_insert_values={"workspace_id": workspace_id},
        )

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
        statement = statement.options(selectinload(Invitation.role_obj)).order_by(
            Invitation.created_at.desc()
        )
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

    @classmethod
    async def accept_invitation_for_user(
        cls,
        session: AsyncSession,
        *,
        user_id: UserID,
        token: str,
    ) -> Membership:
        """Accept a workspace invitation on behalf of ``user_id`` without prior context.

        Resolves the invitation's organization from its workspace, builds a
        user-scoped :class:`Role`, and delegates to :meth:`accept_invitation`.
        Callers that lack organization context (registration, SAML login, the
        public accept route) share this single entry point.

        Args:
            session: Database session.
            user_id: The user accepting the invitation.
            token: The invitation token.

        Returns:
            The created workspace membership.

        Raises:
            TracecatNotFoundError: If the invitation/token is unknown.
            TracecatAuthorizationError: If the invitee email does not match.
            TracecatValidationError: If the invitation is expired or invalid.
        """
        org_id = await session.scalar(
            select(Workspace.organization_id)
            .join(Invitation, Invitation.workspace_id == Workspace.id)  # pyright: ignore[reportArgumentType]
            .where(Invitation.token == token)
        )
        if org_id is None:
            raise TracecatNotFoundError("Invitation not found")

        accept_role = Role(
            type="user",
            user_id=user_id,
            organization_id=org_id,
            service_id="tracecat-api",
        )
        service = cls(session, role=accept_role)
        return await service.accept_invitation(token, user_id)

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

        # Verify the accepting user's email matches the invitation so a leaked
        # token can't enroll a different account.
        user_email = await self.session.scalar(
            select(User.email).where(User.id == user_id)  # pyright: ignore[reportArgumentType, reportCallIssue]
        )
        if user_email is None:
            raise TracecatAuthorizationError("User not found")
        if user_email.lower() != invitation.email.lower():
            raise TracecatAuthorizationError(
                "This invitation was sent to a different email address"
            )

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

        # If not in org, auto-create membership and assign org-member RBAC role
        created_org_membership = False
        if org_membership is None:
            org_membership = OrganizationMembership(
                user_id=user_id,
                organization_id=organization_id,
            )
            self.session.add(org_membership)
            created_org_membership = True
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

        # Create RBAC role assignment for the workspace. Accept is just another
        # workspace-scoped write path: we write the role, then let the
        # reconciler materialize the Membership row as the final step.
        ws_assignment = UserRoleAssignment(
            organization_id=organization_id,
            user_id=user_id,
            workspace_id=invitation.workspace_id,
            role_id=invitation.role_id,
        )
        self.session.add(ws_assignment)

        # If we auto-created org membership, also assign org-member RBAC role
        if created_org_membership:
            org_member_role_result = await self.session.execute(
                select(DBRole).where(
                    DBRole.organization_id == organization_id,
                    DBRole.slug == "organization-member",
                )
            )
            org_member_role = org_member_role_result.scalar_one_or_none()
            if org_member_role is not None:
                org_assignment = UserRoleAssignment(
                    organization_id=organization_id,
                    user_id=user_id,
                    workspace_id=None,
                    role_id=org_member_role.id,
                )
                self.session.add(org_assignment)

        await self.session.commit()

        # Final step: reconcile the membership dial against the role we wrote.
        await MembershipService(self.session).reconcile_workspace_membership(
            user_id, invitation.workspace_id
        )
        membership = await self.session.scalar(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.workspace_id == invitation.workspace_id,
            )
        )
        if membership is None:
            raise TracecatValidationError(
                "Failed to materialize workspace membership after accepting invitation"
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

    @require_scope("workspace:member:invite")
    async def get_invitation_token(
        self,
        workspace_id: WorkspaceID,
        invitation_id: InvitationID,
    ) -> str:
        """Return the token for a pending workspace invitation (copy-link flow).

        Raises:
            TracecatNotFoundError: If the invitation is not found.
            TracecatAuthorizationError: If the invitation is not pending.
        """
        invitation = await self.get_pending_invitation(workspace_id, invitation_id)
        return invitation.token

    @require_scope("workspace:member:invite")
    async def get_pending_invitation(
        self,
        workspace_id: WorkspaceID,
        invitation_id: InvitationID,
    ) -> Invitation:
        """Get a pending workspace invitation (for re-sending the invite email).

        Raises:
            TracecatNotFoundError: If the invitation is not found.
            TracecatAuthorizationError: If the invitation is not pending.
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
            raise TracecatAuthorizationError(
                f"Cannot resend invitation with status '{invitation.status}'"
            )
        return invitation

    async def get_workspace_name(self, workspace_id: WorkspaceID) -> str:
        """Return a workspace's name."""
        result = await self.session.execute(
            select(Workspace.name).where(Workspace.id == workspace_id)
        )
        name = result.scalar_one_or_none()
        if name is None:
            raise TracecatNotFoundError("Workspace not found")
        return name
