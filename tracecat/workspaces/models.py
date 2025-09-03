from __future__ import annotations

from typing import NotRequired, TypedDict

from pydantic import BaseModel, EmailStr, Field, computed_field

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
    allowed_attachment_extensions: NotRequired[list[str] | None]
    allowed_attachment_mime_types: NotRequired[list[str] | None]
    validate_attachment_magic_number: NotRequired[bool | None]


# Schema
class WorkspaceSettingsRead(BaseModel):
    git_repo_url: str | None = None
    workflow_unlimited_timeout_enabled: bool | None = None
    workflow_default_timeout_seconds: int | None = None
    allowed_attachment_extensions: list[str] | None = None
    allowed_attachment_mime_types: list[str] | None = None
    validate_attachment_magic_number: bool | None = None

    @computed_field
    @property
    def effective_allowed_attachment_extensions(self) -> list[str]:
        """Returns workspace-specific extensions if set, otherwise system defaults."""
        if self.allowed_attachment_extensions:
            return self.allowed_attachment_extensions
        return sorted(config.TRACECAT__ALLOWED_ATTACHMENT_EXTENSIONS)

    @computed_field
    @property
    def effective_allowed_attachment_mime_types(self) -> list[str]:
        """Returns workspace-specific MIME types if set, otherwise system defaults."""
        if self.allowed_attachment_mime_types:
            return self.allowed_attachment_mime_types
        return sorted(config.TRACECAT__ALLOWED_ATTACHMENT_MIME_TYPES)


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
    allowed_attachment_extensions: list[str] | None = Field(
        default=None,
        description="Allowed file extensions for attachments (e.g., ['.pdf', '.docx']). Overrides global defaults.",
    )
    allowed_attachment_mime_types: list[str] | None = Field(
        default=None,
        description="Allowed MIME types for attachments (e.g., ['application/pdf', 'image/jpeg']). Overrides global defaults.",
    )
    validate_attachment_magic_number: bool | None = Field(
        default=None,
        description="Whether to validate file content matches declared MIME type using magic number detection. Defaults to true for security.",
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
