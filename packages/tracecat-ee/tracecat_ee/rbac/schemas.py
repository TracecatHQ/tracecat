"""RBAC API schemas for roles, groups, scopes, and assignments."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

from tracecat.authz.enums import ScopeSource
from tracecat.authz.scopes import PRESET_ROLE_SCOPES
from tracecat.core.schemas import Schema

# =============================================================================
# Scope Schemas
# =============================================================================


class ScopeRead(Schema):
    """Read schema for a scope."""

    id: UUID
    name: str
    resource: str
    action: str
    description: str | None = None
    source: ScopeSource
    source_ref: str | None = None
    organization_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class ScopeCreate(BaseModel):
    """Create schema for a custom scope."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=r"^[a-z0-9:_.*-]+$",
        description="Scope name in format resource:action (e.g., 'custom:read')",
    )
    description: str | None = Field(
        None, max_length=512, description="Optional description of the scope"
    )


class ScopeList(BaseModel):
    """Response schema for listing scopes."""

    items: list[ScopeRead]
    total: int


# =============================================================================
# Role Schemas
# =============================================================================


class RoleRead(BaseModel):
    """Read schema for a role."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str | None = None
    description: str | None = None
    organization_id: UUID
    created_at: datetime
    updated_at: datetime
    created_by: UUID | None = None

    @computed_field
    @property
    def is_system(self) -> bool:
        """Whether this is a preset system role."""
        return self.slug in PRESET_ROLE_SCOPES


class RoleReadWithScopes(RoleRead):
    """Read schema for a role with its scopes."""

    scopes: list[ScopeRead] = Field(default_factory=list)


class RoleCreate(BaseModel):
    """Create schema for a custom role."""

    name: str = Field(..., min_length=1, max_length=128, description="Role name")
    description: str | None = Field(
        None, max_length=512, description="Optional description of the role"
    )
    scope_ids: list[UUID] = Field(
        default_factory=list, description="List of scope IDs to assign to the role"
    )


class RoleUpdate(BaseModel):
    """Update schema for a role."""

    name: str | None = Field(
        None, min_length=1, max_length=128, description="Role name"
    )
    description: str | None = Field(
        None, max_length=512, description="Optional description of the role"
    )
    scope_ids: list[UUID] | None = Field(
        None, description="List of scope IDs to assign to the role (replaces existing)"
    )


class RoleList(BaseModel):
    """Response schema for listing roles."""

    items: list[RoleReadWithScopes]
    total: int


# =============================================================================
# Group Schemas
# =============================================================================


class GroupMemberRead(BaseModel):
    """Read schema for a group member."""

    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    email: str
    first_name: str | None = None
    last_name: str | None = None
    added_at: datetime


class GroupRead(BaseModel):
    """Read schema for a group."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None = None
    organization_id: UUID
    created_at: datetime
    updated_at: datetime
    created_by: UUID | None = None


class GroupReadWithMembers(GroupRead):
    """Read schema for a group with its members."""

    members: list[GroupMemberRead] = Field(default_factory=list)
    member_count: int = 0


class GroupCreate(BaseModel):
    """Create schema for a group."""

    name: str = Field(..., min_length=1, max_length=128, description="Group name")
    description: str | None = Field(
        None, max_length=512, description="Optional description of the group"
    )


class GroupUpdate(BaseModel):
    """Update schema for a group."""

    name: str | None = Field(
        None, min_length=1, max_length=128, description="Group name"
    )
    description: str | None = Field(
        None, max_length=512, description="Optional description of the group"
    )


class GroupMemberAdd(BaseModel):
    """Schema for adding a member to a group."""

    user_id: UUID = Field(..., description="User ID to add to the group")


class GroupList(BaseModel):
    """Response schema for listing groups."""

    items: list[GroupReadWithMembers]
    total: int


# =============================================================================
# Group Assignment Schemas
# =============================================================================


class GroupRoleAssignmentRead(BaseModel):
    """Read schema for a group assignment."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    group_id: UUID
    workspace_id: UUID | None = None
    role_id: UUID
    assigned_at: datetime
    assigned_by: UUID | None = None


class GroupRoleAssignmentReadWithDetails(GroupRoleAssignmentRead):
    """Read schema for a group assignment with group and role details."""

    group_name: str
    role_name: str
    workspace_name: str | None = None


class GroupRoleAssignmentCreate(BaseModel):
    """Create schema for a group assignment."""

    group_id: UUID = Field(..., description="Group ID to assign")
    role_id: UUID = Field(..., description="Role ID to assign to the group")
    workspace_id: UUID | None = Field(
        None,
        description="Workspace ID for workspace-level assignment. "
        "If None, creates org-wide assignment.",
    )


class GroupRoleAssignmentUpdate(BaseModel):
    """Update schema for a group assignment (change role only)."""

    role_id: UUID = Field(..., description="New role ID to assign")


class GroupRoleAssignmentList(BaseModel):
    """Response schema for listing group assignments."""

    items: list[GroupRoleAssignmentReadWithDetails]
    total: int


# =============================================================================
# User Role Assignment Schemas
# =============================================================================


class UserRoleAssignmentRead(BaseModel):
    """Read schema for a user role assignment."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    user_id: UUID
    workspace_id: UUID | None = None
    role_id: UUID
    assigned_at: datetime
    assigned_by: UUID | None = None


class UserRoleAssignmentReadWithDetails(UserRoleAssignmentRead):
    """Read schema for a user role assignment with user and role details."""

    user_email: str
    role_name: str
    workspace_name: str | None = None


class UserRoleAssignmentCreate(BaseModel):
    """Create schema for a user role assignment."""

    user_id: UUID = Field(..., description="User ID to assign role to")
    role_id: UUID = Field(..., description="Role ID to assign to the user")
    workspace_id: UUID | None = Field(
        None,
        description="Workspace ID for workspace-level assignment. "
        "If None, creates org-wide assignment.",
    )


class UserRoleAssignmentUpdate(BaseModel):
    """Update schema for a user role assignment (change role only)."""

    role_id: UUID = Field(..., description="New role ID to assign")


class UserRoleAssignmentList(BaseModel):
    """Response schema for listing user role assignments."""

    items: list[UserRoleAssignmentReadWithDetails]
    total: int
