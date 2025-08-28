from __future__ import annotations

from typing import NotRequired, TypedDict

from pydantic import BaseModel, EmailStr, Field

from tracecat import config
from tracecat.auth.models import UserRole
from tracecat.authz.models import WorkspaceRole
from tracecat.identifiers import OwnerID, UserID, WorkspaceID

# === Workspace === #


# DTO
class WorkspaceSettings(TypedDict):
    git_repo_url: NotRequired[str | None]
    workflow_unlimited_timeout_enabled: NotRequired[bool | None]
    workflow_default_timeout_seconds: NotRequired[int | None]


# Schema
class WorkspaceSettingsRead(BaseModel):
    git_repo_url: str | None = None
    workflow_unlimited_timeout_enabled: bool | None = None
    workflow_default_timeout_seconds: int | None = None


class WorkspaceSettingsUpdate(BaseModel):
    git_repo_url: str | None = None
    workflow_unlimited_timeout_enabled: bool | None = Field(
        default=None,
        description="Allow workflows to run indefinitely without timeout constraints. When enabled, individual workflow timeout settings are ignored.",
    )
    workflow_default_timeout_seconds: int | None = Field(
        default=None,
        ge=0,
        description="Default timeout in seconds for workflows in this workspace. Must be greater than or equal to 0.",
    )


# Params
class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    settings: WorkspaceSettingsUpdate | None = None
    owner_id: OwnerID = Field(default=config.TRACECAT__DEFAULT_ORG_ID)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    settings: WorkspaceSettingsUpdate | None = None


class WorkspaceSearch(BaseModel):
    name: str | None = None


# Responses
class WorkspaceReadMinimal(BaseModel):
    id: WorkspaceID
    name: str


class WorkspaceMember(BaseModel):
    user_id: UserID
    first_name: str | None
    last_name: str | None
    email: EmailStr
    org_role: UserRole
    workspace_role: WorkspaceRole


class WorkspaceRead(BaseModel):
    id: WorkspaceID
    name: str
    settings: WorkspaceSettingsRead | None = None
    owner_id: OwnerID


# === Membership === #
class WorkspaceMembershipCreate(BaseModel):
    user_id: UserID
    role: WorkspaceRole = WorkspaceRole.EDITOR


class WorkspaceMembershipUpdate(BaseModel):
    role: WorkspaceRole | None = None


class WorkspaceMembershipRead(BaseModel):
    user_id: UserID
    workspace_id: WorkspaceID
    role: WorkspaceRole
