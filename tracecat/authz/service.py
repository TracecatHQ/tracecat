from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import and_, delete, exists, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql import ColumnElement

from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.contexts import ctx_role
from tracecat.db.locks import (
    derive_lock_key_from_parts,
    pg_advisory_xact_lock_many,
)
from tracecat.db.models import (
    GroupMember,
    GroupRoleAssignment,
    Membership,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.db.rls import workspace_rls_context
from tracecat.exceptions import TracecatConflictError, TracecatValidationError
from tracecat.identifiers import OrganizationID, UserID, WorkspaceID
from tracecat.logger import logger
from tracecat.service import BaseService
from tracecat.workspaces.schemas import (
    WorkspaceMember,
    WorkspaceMembershipCreate,
)


@dataclass
class MembershipWithOrg:
    """Membership with organization ID."""

    membership: Membership
    org_id: OrganizationID


def workspace_scoped_path_exists(
    user_id: UserID | ColumnElement[uuid.UUID] | InstrumentedAttribute[uuid.UUID],
    workspace_id: WorkspaceID,
) -> ColumnElement[bool]:
    """
    Source of truth for the membership invariant. Org-wide assignments
    (``workspace_id IS NULL``) are deliberately excluded. ``user_id`` may be a
    column expression so the predicate can correlate inside a set-based statement.
    """
    direct = exists().where(
        UserRoleAssignment.user_id == user_id,
        UserRoleAssignment.workspace_id == workspace_id,
    )
    return or_(direct, group_scoped_path_exists(user_id, workspace_id))


def group_scoped_path_exists(
    user_id: UserID | ColumnElement[uuid.UUID] | InstrumentedAttribute[uuid.UUID],
    workspace_id: WorkspaceID,
) -> ColumnElement[bool]:
    """Does the user reach W through a group? The group half of
    :func:`workspace_scoped_path_exists`, shared so the two checks can't drift.
    """
    return (
        select(GroupMember.user_id)
        .join(
            GroupRoleAssignment,
            GroupRoleAssignment.group_id == GroupMember.group_id,
        )
        .where(
            GroupMember.user_id == user_id,
            GroupRoleAssignment.workspace_id == workspace_id,
        )
        .exists()
    )


class MembershipService(BaseService):
    """Manage workspace memberships.

    This service optionally accepts a role for authorization-controlled methods
    (like add/update/delete membership). Methods used during the auth flow
    (like get_membership, list_user_memberships) don't require a role.
    """

    service_name = "membership"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session)
        self.role = role or ctx_role.get()

    async def list_memberships(self, workspace_id: WorkspaceID) -> Sequence[Membership]:
        """List all workspace memberships."""
        statement = select(Membership).where(Membership.workspace_id == workspace_id)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def list_workspace_members(
        self, workspace_id: WorkspaceID
    ) -> list[WorkspaceMember]:
        """List workspace members with their roles.

        Displayed role: direct assignment if any, else group-derived, else the
        editor default. ``via_group`` flags members reachable through a group
        (even if they also hold a direct assignment) — the same condition under
        which :meth:`delete_membership` refuses removal, so the UI can gate
        per-row actions on it.
        """
        # One group-derived role per user; min(name) picks deterministically
        # when a user inherits several (Role has no privilege ranking).
        group_role = (
            select(
                GroupMember.user_id.label("user_id"),
                func.min(DBRole.name).label("role_name"),
            )
            .select_from(GroupMember)
            .join(
                GroupRoleAssignment,
                GroupRoleAssignment.group_id == GroupMember.group_id,
            )
            .join(DBRole, DBRole.id == GroupRoleAssignment.role_id)
            .where(GroupRoleAssignment.workspace_id == workspace_id)
            .group_by(GroupMember.user_id)
            .subquery()
        )

        statement = (
            select(
                User,
                # Direct role wins, else group-derived, else default editor.
                func.coalesce(
                    DBRole.name,
                    group_role.c.role_name,
                    literal("Workspace Editor"),
                ).label("role_name"),
                # Same predicate as delete_membership's rejection check, so a
                # user with both a direct and group path reads as via_group
                # (removable=false) — matching what the delete would do.
                group_scoped_path_exists(User.id, workspace_id).label("via_group"),
            )
            .select_from(Membership)
            .join(User, Membership.user_id == User.id)
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .outerjoin(
                UserRoleAssignment,
                and_(
                    UserRoleAssignment.user_id == User.id,
                    UserRoleAssignment.workspace_id == Membership.workspace_id,
                    UserRoleAssignment.organization_id == Workspace.organization_id,
                ),
            )
            .outerjoin(DBRole, DBRole.id == UserRoleAssignment.role_id)
            .outerjoin(group_role, group_role.c.user_id == User.id)
            .where(Membership.workspace_id == workspace_id)
        )
        rows = (await self.session.execute(statement)).all()
        return [
            WorkspaceMember(
                user_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                email=user.email,
                role_name=role_name,
                via_group=bool(via_group),
            )
            for user, role_name, via_group in rows
        ]

    async def get_membership(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> MembershipWithOrg | None:
        """Get a workspace membership with organization ID."""
        statement = (
            select(Membership, Workspace.organization_id)
            .join(Workspace, Membership.workspace_id == Workspace.id)
            .where(
                Membership.user_id == user_id,
                Membership.workspace_id == workspace_id,
            )
        )
        result = await self.session.execute(statement)
        row = result.first()
        if row is None:
            return None
        membership, org_id = row
        return MembershipWithOrg(membership=membership, org_id=org_id)

    async def list_user_memberships(self, user_id: UserID) -> Sequence[Membership]:
        """List all workspace memberships for a specific user.

        This is used by the authorization middleware to cache user permissions.
        """
        statement = select(Membership).where(Membership.user_id == user_id)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def list_user_memberships_with_org(
        self, user_id: UserID
    ) -> Sequence[MembershipWithOrg]:
        """List all workspace memberships for a user with organization IDs."""
        statement = (
            select(Membership, Workspace.organization_id)
            .join(Workspace, Membership.workspace_id == Workspace.id)
            .where(Membership.user_id == user_id)
        )
        result = await self.session.execute(statement)
        return [
            MembershipWithOrg(membership=membership, org_id=org_id)
            for membership, org_id in result.all()
        ]

    @require_scope("workspace:member:invite")
    async def create_membership(
        self,
        workspace_id: WorkspaceID,
        params: WorkspaceMembershipCreate,
    ) -> None:
        """Create a workspace membership.

        Note: The authorization cache is request-scoped, so changes will be
        reflected in subsequent requests automatically.
        """
        # Resolve workspace org + default role in one DB read.
        org_role_stmt = (
            select(Workspace.organization_id, DBRole.id)
            .join(
                DBRole,
                and_(
                    DBRole.organization_id == Workspace.organization_id,
                    DBRole.slug == "workspace-editor",
                ),
            )
            .where(Workspace.id == workspace_id)
        )
        org_role_row = (await self.session.execute(org_role_stmt)).first()
        if org_role_row is None:
            raise TracecatValidationError("Workspace or default role not found")
        organization_id, role_id = org_role_row

        # Write the role path; the reconciler derives the Membership row from it.
        # DO NOTHING keeps any existing direct assignment (e.g. a custom admin
        # role) instead of downgrading it to the default editor.
        await self.session.execute(
            pg_insert(UserRoleAssignment)
            .values(
                organization_id=organization_id,
                user_id=params.user_id,
                workspace_id=workspace_id,
                role_id=role_id,
                assigned_by=self.role.user_id if self.role else None,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    UserRoleAssignment.user_id,
                    UserRoleAssignment.workspace_id,
                ]
            )
        )
        await self.session.commit()
        await self.reconcile_workspace_membership(params.user_id, workspace_id)

    @require_scope("workspace:member:remove")
    async def delete_membership(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> None:
        """Remove the user's direct role path to the workspace, then reconcile.

        Rejects with a conflict if access is group-derived: dropping direct
        assignments would leave the group path intact and the user would
        reappear, so they must be removed via the group instead.

        Note: The authorization cache is request-scoped, so changes will be
        reflected in subsequent requests automatically.
        """
        # A group-derived path survives this removal, so reject before mutating.
        has_group_path = await self.session.scalar(
            select(group_scoped_path_exists(user_id, workspace_id))
        )
        if has_group_path:
            raise TracecatConflictError(
                "User's workspace access is granted through a group. Remove the "
                "user from the group or change the group's role assignment."
            )

        # Remove the direct role path, then reconcile (drops Membership only if
        # no other ws-scoped path remains).
        await self.session.execute(
            delete(UserRoleAssignment).where(
                UserRoleAssignment.workspace_id == workspace_id,
                UserRoleAssignment.user_id == user_id,
            )
        )
        await self.session.commit()
        await self.reconcile_workspace_membership(user_id, workspace_id)

    async def reconcile_workspace_membership(
        self, user_id: UserID, workspace_id: WorkspaceID
    ) -> None:
        """Upsert/delete Membership(user, W) to match workspace-scoped RBAC.

        Single-pair convenience over :meth:`reconcile_users_for_workspace`,
        which holds the invariant logic.
        """
        await self.reconcile_users_for_workspace([user_id], workspace_id)

    async def reconcile_group_members(
        self, group_id: uuid.UUID, workspace_id: WorkspaceID
    ) -> None:
        """Reconcile every member of a group against one workspace.

        Fans a group role-assignment change out to all current members via
        :meth:`reconcile_users_for_workspace`, which holds the invariant logic.
        """
        member_ids = (
            (
                await self.session.execute(
                    select(GroupMember.user_id).where(GroupMember.group_id == group_id)
                )
            )
            .scalars()
            .all()
        )
        await self.reconcile_users_for_workspace(member_ids, workspace_id)

    async def reconcile_users_for_workspace(
        self, user_ids: Sequence[UserID], workspace_id: WorkspaceID
    ) -> None:
        """Reconcile Membership rows for a set of users against one workspace.

        The single implementation of the membership invariant: a row exists iff
        the user holds at least one ws-scoped role path (direct or via group).
        Set-based — one upsert + one delete + one commit for the whole batch —
        and idempotent. Serialized against concurrent reconciles by per-(user,
        W) transaction-scoped advisory locks, acquired in sorted order in one
        statement. Runs inside a temporary ws-scoped RLS context so org-scoped
        callers (RBAC routes) can write the rows under enforce mode. The caller
        commits the RBAC writes that motivated the reconcile.
        """
        if not user_ids:
            return
        lock_keys = [
            derive_lock_key_from_parts("membership", str(user_id), str(workspace_id))
            for user_id in set(user_ids)
        ]
        async with workspace_rls_context(self.session, workspace_id):
            # Transaction-scoped locks; released by this method's commit.
            await pg_advisory_xact_lock_many(self.session, lock_keys)
            # Materialize membership for users that hold a ws-scoped path.
            await self.session.execute(
                pg_insert(Membership)
                .from_select(
                    ["user_id", "workspace_id"],
                    select(User.id, literal(workspace_id)).where(  # pyright: ignore[reportCallIssue,reportArgumentType]
                        User.id.in_(user_ids),  # pyright: ignore[reportAttributeAccessIssue]
                        workspace_scoped_path_exists(User.id, workspace_id),  # pyright: ignore[reportArgumentType]
                    ),
                )
                .on_conflict_do_nothing(
                    index_elements=[Membership.user_id, Membership.workspace_id]
                )
            )
            # Drop membership for users with no remaining ws-scoped path.
            await self.session.execute(
                delete(Membership).where(
                    Membership.workspace_id == workspace_id,
                    Membership.user_id.in_(user_ids),
                    ~workspace_scoped_path_exists(Membership.user_id, workspace_id),
                )
            )
            await self.session.commit()
        logger.debug(
            "Reconciled users for workspace",
            user_count=len(set(user_ids)),
            workspace_id=workspace_id,
        )
