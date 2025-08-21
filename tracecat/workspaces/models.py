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


# Schema
class WorkspaceSettingsRead(BaseModel):
    git_repo_url: str | None = None


class WorkspaceSettingsUpdate(BaseModel):
    git_repo_url: str | None = None


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
    n_members: int


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
    n_members: int
    members: list[WorkspaceMember]


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
