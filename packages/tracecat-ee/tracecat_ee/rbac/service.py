"""RBAC service for managing roles, groups, scopes, and assignments."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from tracecat.audit.logger import audit_log
from tracecat.authz.controls import validate_scope_string
from tracecat.authz.enums import ScopeSource
from tracecat.db.models import (
    Group,
    GroupAssignment,
    GroupMember,
    OrganizationMembership,
    RoleScope,
    Scope,
    User,
    UserRoleAssignment,
    Workspace,
)
from tracecat.db.models import (
    Role as RoleModel,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers import UserID, WorkspaceID
from tracecat.service import BaseOrgService


class RBACService(BaseOrgService):
    """Service for managing RBAC entities and computing effective scopes."""

    service_name = "rbac"

    # =========================================================================
    # Scope Management
    # =========================================================================

    async def list_scopes(
        self,
        *,
        include_system: bool = True,
        source: ScopeSource | None = None,
    ) -> Sequence[Scope]:
        """List scopes available to the organization.

        Args:
            include_system: Include system/registry scopes (org_id=NULL)
            source: Filter by scope source

        Returns:
            List of Scope objects
        """
        # Build query for org-specific scopes
        conditions = [Scope.organization_id == self.organization_id]

        if include_system:
            # Include system scopes (organization_id IS NULL)
            conditions = [
                (Scope.organization_id == self.organization_id)
                | (Scope.organization_id.is_(None))
            ]

        stmt = select(Scope).where(*conditions)

        if source is not None:
            stmt = stmt.where(Scope.source == source)

        stmt = stmt.order_by(Scope.name)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_scope(self, scope_id: UserID) -> Scope:
        """Get a scope by ID."""
        stmt = select(Scope).where(
            Scope.id == scope_id,
            (Scope.organization_id == self.organization_id)
            | (Scope.organization_id.is_(None)),
        )
        result = await self.session.execute(stmt)
        scope = result.scalar_one_or_none()
        if scope is None:
            raise TracecatNotFoundError("Scope not found")
        return scope

    @audit_log(resource_type="rbac_scope", action="create")
    async def create_scope(
        self,
        *,
        name: str,
        description: str | None = None,
    ) -> Scope:
        """Create a custom scope for the organization.

        Args:
            name: Scope name in format resource:action
            description: Optional description

        Returns:
            Created Scope object
        """
        if not validate_scope_string(name):
            raise TracecatValidationError(
                "Invalid scope name. Must be lowercase with only alphanumeric, "
                "colon, underscore, dot, dash, and asterisk characters."
            )

        # Parse resource and action from scope name
        parts = name.rsplit(":", 1)
        if len(parts) != 2:
            raise TracecatValidationError(
                "Scope name must be in format 'resource:action'"
            )
        resource, action = parts

        scope = Scope(
            name=name,
            resource=resource,
            action=action,
            description=description,
            source=ScopeSource.CUSTOM,
            organization_id=self.organization_id,
        )
        self.session.add(scope)
        await self.session.commit()
        await self.session.refresh(scope)
        return scope

    @audit_log(resource_type="rbac_scope", action="delete", resource_id_attr="scope_id")
    async def delete_scope(self, scope_id: UserID) -> None:
        """Delete a custom scope.

        Only custom scopes (source=CUSTOM) can be deleted.
        """
        scope = await self.get_scope(scope_id)

        if scope.source != ScopeSource.CUSTOM:
            raise TracecatAuthorizationError(
                f"Cannot delete {scope.source.value} scopes"
            )

        if scope.organization_id != self.organization_id:
            raise TracecatAuthorizationError("Cannot delete scope from another org")

        await self.session.delete(scope)
        await self.session.commit()

    # =========================================================================
    # Role Management
    # =========================================================================

    async def list_roles(self) -> Sequence[RoleModel]:
        """List roles for the organization."""
        stmt = (
            select(RoleModel)
            .where(RoleModel.organization_id == self.organization_id)
            .options(selectinload(RoleModel.scopes))
            .order_by(RoleModel.name)
        )

        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_role(self, role_id: UserID) -> RoleModel:
        """Get a role by ID with its scopes."""
        stmt = (
            select(RoleModel)
            .where(
                RoleModel.id == role_id,
                RoleModel.organization_id == self.organization_id,
            )
            .options(selectinload(RoleModel.scopes))
        )
        result = await self.session.execute(stmt)
        role = result.scalar_one_or_none()
        if role is None:
            raise TracecatNotFoundError("Role not found")
        return role

    @audit_log(resource_type="rbac_role", action="create")
    async def create_role(
        self,
        *,
        name: str,
        description: str | None = None,
        scope_ids: list[UserID] | None = None,
    ) -> RoleModel:
        """Create a custom role for the organization."""
        if self.role.user_id is None:
            raise TracecatAuthorizationError("User ID required to create role")

        role = RoleModel(
            name=name,
            description=description,
            organization_id=self.organization_id,
            created_by=self.role.user_id,
        )
        self.session.add(role)
        await self.session.flush()  # Get the role ID

        # Add scopes if provided
        if scope_ids:
            await self._set_role_scopes(role.id, scope_ids)

        await self.session.commit()
        await self.session.refresh(role, ["scopes"])
        return role

    @audit_log(resource_type="rbac_role", action="update", resource_id_attr="role_id")
    async def update_role(
        self,
        role_id: UserID,
        *,
        name: str | None = None,
        description: str | None = None,
        scope_ids: list[UserID] | None = None,
    ) -> RoleModel:
        """Update a role.

        System roles (admin, editor, viewer) cannot have their scopes modified.
        """
        role = await self.get_role(role_id)

        # System roles cannot have scopes modified
        if role.slug in {"admin", "editor", "viewer"} and scope_ids is not None:
            raise TracecatAuthorizationError("Cannot modify scopes of system roles")

        if name is not None:
            role.name = name
        if description is not None:
            role.description = description

        if scope_ids is not None:
            await self._set_role_scopes(role.id, scope_ids)

        await self.session.commit()
        await self.session.refresh(role, ["scopes"])
        return role

    @audit_log(resource_type="rbac_role", action="delete", resource_id_attr="role_id")
    async def delete_role(self, role_id: UserID) -> None:
        """Delete a role.

        System roles (admin, editor, viewer) cannot be deleted.
        """
        role = await self.get_role(role_id)

        # System roles cannot be deleted
        if role.slug in {"admin", "editor", "viewer"}:
            raise TracecatAuthorizationError("Cannot delete system roles")

        # Check if role is in use by any group assignments
        stmt = select(func.count()).where(GroupAssignment.role_id == role_id)
        result = await self.session.execute(stmt)
        group_count = result.scalar() or 0
        if group_count > 0:
            raise TracecatValidationError(
                "Cannot delete role that is assigned to groups. "
                "Remove all group assignments first."
            )

        # Check if role is in use by any user role assignments
        stmt = select(func.count()).where(UserRoleAssignment.role_id == role_id)
        result = await self.session.execute(stmt)
        user_count = result.scalar() or 0
        if user_count > 0:
            raise TracecatValidationError(
                "Cannot delete role that is assigned to users. "
                "Remove all user assignments first."
            )

        await self.session.delete(role)
        await self.session.commit()

    async def _set_role_scopes(self, role_id: UserID, scope_ids: list[UserID]) -> None:
        """Set the scopes for a role (replaces existing)."""
        # Delete existing role-scope associations
        await self.session.execute(
            delete(RoleScope).where(RoleScope.role_id == role_id)
        )

        # Add new associations
        for scope_id in scope_ids:
            # Verify scope exists and is accessible
            await self.get_scope(scope_id)
            role_scope = RoleScope(role_id=role_id, scope_id=scope_id)
            self.session.add(role_scope)

    # =========================================================================
    # Group Management
    # =========================================================================

    async def list_groups(self) -> Sequence[Group]:
        """List groups for the organization."""
        stmt = (
            select(Group)
            .where(Group.organization_id == self.organization_id)
            .options(selectinload(Group.members))
            .order_by(Group.name)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_group(self, group_id: UserID) -> Group:
        """Get a group by ID with its members."""
        stmt = (
            select(Group)
            .where(
                Group.id == group_id,
                Group.organization_id == self.organization_id,
            )
            .options(selectinload(Group.members))
        )
        result = await self.session.execute(stmt)
        group = result.scalar_one_or_none()
        if group is None:
            raise TracecatNotFoundError("Group not found")
        return group

    @audit_log(resource_type="rbac_group", action="create")
    async def create_group(
        self,
        *,
        name: str,
        description: str | None = None,
    ) -> Group:
        """Create a group for the organization."""
        group = Group(
            name=name,
            description=description,
            organization_id=self.organization_id,
            created_by=self.role.user_id,
        )
        self.session.add(group)
        await self.session.commit()
        await self.session.refresh(group, ["members"])
        return group

    @audit_log(resource_type="rbac_group", action="update", resource_id_attr="group_id")
    async def update_group(
        self,
        group_id: UserID,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Group:
        """Update a group."""
        group = await self.get_group(group_id)

        if name is not None:
            group.name = name
        if description is not None:
            group.description = description

        await self.session.commit()
        await self.session.refresh(group, ["members"])
        return group

    @audit_log(resource_type="rbac_group", action="delete", resource_id_attr="group_id")
    async def delete_group(self, group_id: UserID) -> None:
        """Delete a group."""
        group = await self.get_group(group_id)
        await self.session.delete(group)
        await self.session.commit()

    @audit_log(
        resource_type="rbac_group_member", action="create", resource_id_attr="group_id"
    )
    async def add_group_member(self, group_id: UserID, user_id: UserID) -> None:
        """Add a user to a group."""
        # Verify group exists
        await self.get_group(group_id)

        # Verify user belongs to this organization
        stmt = select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.organization_id == self.organization_id,
        )
        result = await self.session.execute(stmt)
        if result.scalar_one_or_none() is None:
            raise TracecatNotFoundError("User not found in organization")

        # Check if already a member
        stmt = select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        if result.scalar_one_or_none() is not None:
            raise TracecatValidationError("User is already a member of this group")

        member = GroupMember(group_id=group_id, user_id=user_id)
        self.session.add(member)
        await self.session.commit()

    @audit_log(
        resource_type="rbac_group_member", action="delete", resource_id_attr="group_id"
    )
    async def remove_group_member(self, group_id: UserID, user_id: UserID) -> None:
        """Remove a user from a group."""
        stmt = select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        member = result.scalar_one_or_none()
        if member is None:
            raise TracecatNotFoundError("Group member not found")

        await self.session.delete(member)
        await self.session.commit()

    async def list_group_members(
        self, group_id: UserID
    ) -> Sequence[tuple[User, GroupMember]]:
        """List members of a group with their membership info."""
        stmt = (
            select(User, GroupMember)
            .join(GroupMember, GroupMember.user_id == User.id)
            .where(GroupMember.group_id == group_id)
            .order_by(User.email)
        )
        result = await self.session.execute(stmt)
        return result.tuples().all()

    # =========================================================================
    # Group Assignment Management
    # =========================================================================

    async def list_assignments(
        self,
        *,
        group_id: UserID | None = None,
        workspace_id: WorkspaceID | None = None,
    ) -> Sequence[GroupAssignment]:
        """List group assignments for the organization."""
        stmt = (
            select(GroupAssignment)
            .where(GroupAssignment.organization_id == self.organization_id)
            .options(
                selectinload(GroupAssignment.group),
                selectinload(GroupAssignment.role),
                selectinload(GroupAssignment.workspace),
            )
        )

        if group_id is not None:
            stmt = stmt.where(GroupAssignment.group_id == group_id)
        if workspace_id is not None:
            stmt = stmt.where(GroupAssignment.workspace_id == workspace_id)

        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_assignment(self, assignment_id: UserID) -> GroupAssignment:
        """Get a group assignment by ID."""
        stmt = (
            select(GroupAssignment)
            .where(
                GroupAssignment.id == assignment_id,
                GroupAssignment.organization_id == self.organization_id,
            )
            .options(
                selectinload(GroupAssignment.group),
                selectinload(GroupAssignment.role),
                selectinload(GroupAssignment.workspace),
            )
        )
        result = await self.session.execute(stmt)
        assignment = result.scalar_one_or_none()
        if assignment is None:
            raise TracecatNotFoundError("Group assignment not found")
        return assignment

    @audit_log(resource_type="rbac_assignment", action="create")
    async def create_assignment(
        self,
        *,
        group_id: UserID,
        role_id: UserID,
        workspace_id: WorkspaceID | None = None,
    ) -> GroupAssignment:
        """Create a group assignment.

        Args:
            group_id: Group to assign
            role_id: Role to assign to the group
            workspace_id: Workspace for workspace-level assignment (None for org-wide)

        Returns:
            Created GroupAssignment
        """
        # Verify group exists
        await self.get_group(group_id)

        # Verify role exists
        await self.get_role(role_id)

        # Verify workspace exists if provided
        if workspace_id is not None:
            stmt = select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.organization_id == self.organization_id,
            )
            result = await self.session.execute(stmt)
            if result.scalar_one_or_none() is None:
                raise TracecatNotFoundError("Workspace not found")

        assignment = GroupAssignment(
            organization_id=self.organization_id,
            group_id=group_id,
            role_id=role_id,
            workspace_id=workspace_id,
            assigned_by=self.role.user_id,
        )
        self.session.add(assignment)
        await self.session.commit()
        await self.session.refresh(assignment, ["group", "role", "workspace"])
        return assignment

    @audit_log(
        resource_type="rbac_assignment",
        action="update",
        resource_id_attr="assignment_id",
    )
    async def update_assignment(
        self,
        assignment_id: UserID,
        *,
        role_id: UserID,
    ) -> GroupAssignment:
        """Update a group assignment (change role)."""
        assignment = await self.get_assignment(assignment_id)

        # Verify new role exists
        await self.get_role(role_id)

        assignment.role_id = role_id
        await self.session.commit()
        await self.session.refresh(assignment, ["group", "role", "workspace"])
        return assignment

    @audit_log(
        resource_type="rbac_assignment",
        action="delete",
        resource_id_attr="assignment_id",
    )
    async def delete_assignment(self, assignment_id: UserID) -> None:
        """Delete a group assignment."""
        assignment = await self.get_assignment(assignment_id)
        await self.session.delete(assignment)
        await self.session.commit()

    # =========================================================================
    # User Role Assignment Management
    # =========================================================================

    async def list_user_assignments(
        self,
        *,
        user_id: UserID | None = None,
        workspace_id: WorkspaceID | None = None,
    ) -> Sequence[UserRoleAssignment]:
        """List user role assignments for the organization."""
        stmt = (
            select(UserRoleAssignment)
            .where(UserRoleAssignment.organization_id == self.organization_id)
            .options(
                selectinload(UserRoleAssignment.user),
                selectinload(UserRoleAssignment.role),
                selectinload(UserRoleAssignment.workspace),
            )
        )

        if user_id is not None:
            stmt = stmt.where(UserRoleAssignment.user_id == user_id)
        if workspace_id is not None:
            stmt = stmt.where(UserRoleAssignment.workspace_id == workspace_id)

        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_user_assignment(self, assignment_id: UserID) -> UserRoleAssignment:
        """Get a user role assignment by ID."""
        stmt = (
            select(UserRoleAssignment)
            .where(
                UserRoleAssignment.id == assignment_id,
                UserRoleAssignment.organization_id == self.organization_id,
            )
            .options(
                selectinload(UserRoleAssignment.user),
                selectinload(UserRoleAssignment.role),
                selectinload(UserRoleAssignment.workspace),
            )
        )
        result = await self.session.execute(stmt)
        assignment = result.scalar_one_or_none()
        if assignment is None:
            raise TracecatNotFoundError("User role assignment not found")
        return assignment

    @audit_log(resource_type="rbac_user_assignment", action="create")
    async def create_user_assignment(
        self,
        *,
        user_id: UserID,
        role_id: UserID,
        workspace_id: WorkspaceID | None = None,
    ) -> UserRoleAssignment:
        """Create a user role assignment.

        Args:
            user_id: User to assign role to
            role_id: Role to assign to the user
            workspace_id: Workspace for workspace-level assignment (None for org-wide)

        Returns:
            Created UserRoleAssignment
        """
        # Verify user exists
        stmt = select(User).where(User.id == user_id)  # pyright: ignore[reportArgumentType]
        result = await self.session.execute(stmt)
        if result.scalar_one_or_none() is None:
            raise TracecatNotFoundError("User not found")

        # Verify role exists
        await self.get_role(role_id)

        # Verify workspace exists if provided
        if workspace_id is not None:
            stmt = select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.organization_id == self.organization_id,
            )
            result = await self.session.execute(stmt)
            if result.scalar_one_or_none() is None:
                raise TracecatNotFoundError("Workspace not found")

        assignment = UserRoleAssignment(
            organization_id=self.organization_id,
            user_id=user_id,
            role_id=role_id,
            workspace_id=workspace_id,
            assigned_by=self.role.user_id,
        )
        self.session.add(assignment)
        await self.session.commit()
        await self.session.refresh(assignment, ["user", "role", "workspace"])
        return assignment

    @audit_log(
        resource_type="rbac_user_assignment",
        action="update",
        resource_id_attr="assignment_id",
    )
    async def update_user_assignment(
        self,
        assignment_id: UserID,
        *,
        role_id: UserID,
    ) -> UserRoleAssignment:
        """Update a user role assignment (change role)."""
        assignment = await self.get_user_assignment(assignment_id)

        # Verify new role exists
        await self.get_role(role_id)

        assignment.role_id = role_id
        await self.session.commit()
        await self.session.refresh(assignment, ["user", "role", "workspace"])
        return assignment

    @audit_log(
        resource_type="rbac_user_assignment",
        action="delete",
        resource_id_attr="assignment_id",
    )
    async def delete_user_assignment(self, assignment_id: UserID) -> None:
        """Delete a user role assignment."""
        assignment = await self.get_user_assignment(assignment_id)
        await self.session.delete(assignment)
        await self.session.commit()

    async def get_user_role_scopes(
        self,
        user_id: UserID,
        *,
        workspace_id: WorkspaceID | None = None,
    ) -> frozenset[str]:
        """Compute the scopes a user has from direct role assignments.

        Args:
            user_id: User to compute scopes for
            workspace_id: If provided, include workspace-specific user assignments

        Returns:
            Frozenset of scope name strings
        """
        # Query to get all scope names from user's direct role assignments
        # This joins: UserRoleAssignment -> Role -> RoleScope -> Scope
        stmt = (
            select(Scope.name)
            .select_from(UserRoleAssignment)
            .join(RoleModel, UserRoleAssignment.role_id == RoleModel.id)
            .join(RoleScope, RoleScope.role_id == RoleModel.id)
            .join(Scope, RoleScope.scope_id == Scope.id)
            .where(
                UserRoleAssignment.user_id == user_id,
                UserRoleAssignment.organization_id == self.organization_id,
            )
        )

        # Filter assignments by scope:
        # - Org-wide assignments (workspace_id IS NULL) always apply
        # - Workspace-specific assignments only apply if requesting that workspace
        if workspace_id is not None:
            stmt = stmt.where(
                (UserRoleAssignment.workspace_id.is_(None))
                | (UserRoleAssignment.workspace_id == workspace_id)
            )
        else:
            # Only org-wide assignments
            stmt = stmt.where(UserRoleAssignment.workspace_id.is_(None))

        result = await self.session.execute(stmt)
        scope_names = result.scalars().all()

        return frozenset(scope_names)

    # =========================================================================
    # Scope Computation (for auth layer)
    # =========================================================================

    async def get_group_scopes(
        self,
        user_id: UserID,
        *,
        workspace_id: WorkspaceID | None = None,
    ) -> frozenset[str]:
        """Compute the scopes a user has from group memberships.

        This method:
        1. Finds all groups the user belongs to
        2. Gets all role assignments for those groups (org-wide and workspace-specific)
        3. Collects all scope names from those roles

        Args:
            user_id: User to compute scopes for
            workspace_id: If provided, include workspace-specific group assignments

        Returns:
            Frozenset of scope name strings
        """
        # Query to get all scope names from user's group memberships and role assignments
        # This joins: GroupMember -> Group -> GroupAssignment -> Role -> RoleScope -> Scope
        stmt = (
            select(Scope.name)
            .select_from(GroupMember)
            .join(Group, GroupMember.group_id == Group.id)
            .join(GroupAssignment, GroupAssignment.group_id == Group.id)
            .join(RoleModel, GroupAssignment.role_id == RoleModel.id)
            .join(RoleScope, RoleScope.role_id == RoleModel.id)
            .join(Scope, RoleScope.scope_id == Scope.id)
            .where(
                GroupMember.user_id == user_id,
                Group.organization_id == self.organization_id,
            )
        )

        # Filter assignments by scope:
        # - Org-wide assignments (workspace_id IS NULL) always apply
        # - Workspace-specific assignments only apply if requesting that workspace
        if workspace_id is not None:
            stmt = stmt.where(
                (GroupAssignment.workspace_id.is_(None))
                | (GroupAssignment.workspace_id == workspace_id)
            )
        else:
            # Only org-wide assignments
            stmt = stmt.where(GroupAssignment.workspace_id.is_(None))

        result = await self.session.execute(stmt)
        scope_names = result.scalars().all()

        return frozenset(scope_names)

    async def get_user_effective_scopes(
        self,
        user_id: UserID,
        *,
        workspace_id: WorkspaceID | None = None,
    ) -> dict[str, list[str]]:
        """Get detailed breakdown of a user's effective scopes.

        Returns a dictionary with:
        - group_scopes: Scopes from group memberships
        - user_role_scopes: Scopes from direct user role assignments

        Note: Org role and workspace role scopes are computed elsewhere
        (in compute_effective_scopes) as they depend on the Role object.
        """
        group_scopes = await self.get_group_scopes(user_id, workspace_id=workspace_id)
        user_role_scopes = await self.get_user_role_scopes(
            user_id, workspace_id=workspace_id
        )

        return {
            "group_scopes": sorted(group_scopes),
            "user_role_scopes": sorted(user_role_scopes),
        }
