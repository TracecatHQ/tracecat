from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import and_, delete, exists, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.contexts import ctx_role
from tracecat.db.locks import derive_lock_key_from_parts, pg_advisory_lock
from tracecat.db.models import (
    GroupMember,
    GroupRoleAssignment,
    Membership,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import Role as DBRole
from tracecat.exceptions import TracecatValidationError
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
    user_id: UserID | ColumnElement[uuid.UUID], workspace_id: WorkspaceID
) -> ColumnElement[bool]:
    """SQL EXISTS predicate: does the user hold a workspace-scoped role path to W?

    A workspace-scoped path is either:
      - a direct ``UserRoleAssignment(user, role, workspace_id=W)``, or
      - membership in a group that holds a ``GroupRoleAssignment(group, role,
        workspace_id=W)``.

    Org-wide assignments (``workspace_id IS NULL``) grant access via scopes but
    are deliberately NOT membership-materializing, so they are excluded here.

    This is the single source of truth for the membership invariant. The runtime
    reconciler and the backfill migration both use this exact predicate.

    ``user_id`` may be a literal or a column expression (e.g. an outer
    ``GroupMember.user_id``), so the predicate can be correlated inside a
    set-based statement.
    """
    direct = exists().where(
        UserRoleAssignment.user_id == user_id,
        UserRoleAssignment.workspace_id == workspace_id,
    )
    via_group = (
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
    return or_(direct, via_group)


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
        """List all workspace members with their workspace roles from RBAC."""
        statement = (
            select(
                User,
                func.coalesce(DBRole.name, literal("Workspace Editor")).label(
                    "role_name"
                ),
            )
            .select_from(Membership)
            .join(User, Membership.user_id == User.id)  # pyright: ignore[reportArgumentType]
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .outerjoin(
                UserRoleAssignment,
                and_(
                    UserRoleAssignment.user_id == User.id,  # pyright: ignore[reportArgumentType]
                    UserRoleAssignment.workspace_id == Membership.workspace_id,
                    UserRoleAssignment.organization_id == Workspace.organization_id,
                ),
            )
            .outerjoin(DBRole, DBRole.id == UserRoleAssignment.role_id)
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
            )
            for user, role_name in rows
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

        # Heal stale direct assignments left behind by prior failed remove flows.
        await self.session.execute(
            delete(UserRoleAssignment).where(
                UserRoleAssignment.user_id == params.user_id,
                UserRoleAssignment.workspace_id == workspace_id,
            )
        )

        # Write the workspace-scoped role path; the reconciler derives the
        # Membership row from it (single write path for the membership dial).
        self.session.add(
            UserRoleAssignment(
                organization_id=organization_id,
                user_id=params.user_id,
                workspace_id=workspace_id,
                role_id=role_id,
                assigned_by=self.role.user_id if self.role else None,
            )
        )
        await self.session.commit()
        await self.reconcile_workspace_membership(params.user_id, workspace_id)

    @require_scope("workspace:member:remove")
    async def delete_membership(
        self, workspace_id: WorkspaceID, user_id: UserID
    ) -> None:
        """Delete a workspace membership.

        Note: The authorization cache is request-scoped, so changes will be
        reflected in subsequent requests automatically.
        """
        # Remove the direct workspace-scoped role path, then reconcile. The
        # reconciler deletes the Membership row only if no other ws-scoped path
        # (e.g. a group assignment) still holds it.
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
        """Drive the ``Membership`` dial in lockstep with workspace-scoped RBAC.

        Membership(user, W) must exist iff the user holds at least one
        workspace-scoped role path to W (direct or via a group). This consults
        :func:`workspace_scoped_path_exists` and upserts or deletes the
        ``Membership`` row accordingly.

        Idempotent. Serialized per (user, W) with an advisory lock so concurrent
        reconciles cannot race into a contradictory state. The unique
        constraint (composite PK) is the only other DB backstop; no triggers, no
        background job.

        The caller is responsible for committing any RBAC writes that motivated
        the reconcile. This method commits its own membership change.
        """
        lock_key = derive_lock_key_from_parts(
            "membership", str(user_id), str(workspace_id)
        )
        async with pg_advisory_lock(self.session, lock_key):
            has_path = await self.session.scalar(
                select(workspace_scoped_path_exists(user_id, workspace_id))
            )
            if has_path:
                # Upsert: presence fact, no extra columns to update.
                await self.session.execute(
                    pg_insert(Membership)
                    .values(user_id=user_id, workspace_id=workspace_id)
                    .on_conflict_do_nothing(
                        index_elements=[Membership.user_id, Membership.workspace_id]
                    )
                )
            else:
                await self.session.execute(
                    delete(Membership).where(
                        Membership.user_id == user_id,
                        Membership.workspace_id == workspace_id,
                    )
                )
            await self.session.commit()
            logger.debug(
                "Reconciled workspace membership",
                user_id=user_id,
                workspace_id=workspace_id,
                is_member=bool(has_path),
            )

    async def reconcile_group_members(
        self, group_id: uuid.UUID, workspace_id: WorkspaceID
    ) -> None:
        """Reconcile every member of a group against one workspace.

        Used by group-level admin mutations: a single group role assignment
        change fans out to all current group members for the affected
        workspace.

        Set-based rather than per-member: two statements (upsert + delete) under
        a single group-level advisory lock, constant in the group size. This is
        the fanout path that a bulk membership sync (e.g. SCIM) would hammer, so
        it must not degrade to O(N) round trips. The group-level lock serializes
        concurrent fanouts for the same group; per-user direct writes still
        reconcile under their own (user, W) lock and self-heal.
        """
        # Members of this group, as a subquery. The outer statements never range
        # over ``GroupMember`` directly, so the predicate's own ``group_member``
        # reference cannot auto-correlate to it (no alias needed).
        group_member_ids = select(GroupMember.user_id).where(
            GroupMember.group_id == group_id
        )

        lock_key = derive_lock_key_from_parts(
            "membership_group", str(group_id), str(workspace_id)
        )
        async with pg_advisory_lock(self.session, lock_key):
            # Materialize membership for members that now hold a ws-scoped path.
            await self.session.execute(
                pg_insert(Membership)
                .from_select(
                    ["user_id", "workspace_id"],
                    select(User.id, literal(workspace_id)).where(  # pyright: ignore[reportCallIssue, reportArgumentType]
                        User.id.in_(group_member_ids),  # pyright: ignore[reportAttributeAccessIssue]
                        workspace_scoped_path_exists(User.id, workspace_id),  # pyright: ignore[reportArgumentType]
                    ),
                )
                .on_conflict_do_nothing(
                    index_elements=[Membership.user_id, Membership.workspace_id]
                )
            )
            # Drop membership for members that no longer hold any ws-scoped path.
            await self.session.execute(
                delete(Membership).where(
                    Membership.workspace_id == workspace_id,
                    Membership.user_id.in_(group_member_ids),
                    ~workspace_scoped_path_exists(Membership.user_id, workspace_id),  # pyright: ignore[reportArgumentType]
                )
            )
            await self.session.commit()
            logger.debug(
                "Reconciled group members",
                group_id=group_id,
                workspace_id=workspace_id,
            )
