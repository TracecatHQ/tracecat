from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, NotRequired, TypedDict
from uuid import UUID

from pydantic import EmailStr, Field, computed_field, field_validator

from tracecat import config
from tracecat.core.schemas import Schema
from tracecat.git.constants import GIT_SSH_URL_REGEX
from tracecat.identifiers import InvitationID, OrganizationID, UserID, WorkspaceID
from tracecat.invitations.enums import InvitationStatus

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
class WorkspaceSettingsRead(Schema):
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
        if self.allowed_attachment_extensions is not None:
            return self.allowed_attachment_extensions
        return sorted(config.TRACECAT__ALLOWED_ATTACHMENT_EXTENSIONS)

    @computed_field
    @property
    def effective_allowed_attachment_mime_types(self) -> list[str]:
        """Returns workspace-specific MIME types if set, otherwise system defaults."""
        if self.allowed_attachment_mime_types is not None:
            return self.allowed_attachment_mime_types
        return sorted(config.TRACECAT__ALLOWED_ATTACHMENT_MIME_TYPES)


class WorkspaceSettingsUpdate(Schema):
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

    @field_validator("git_repo_url", mode="before")
    @classmethod
    def validate_git_repo_url(cls, value: str | None) -> str | None:
        """Ensure workspace git repo URLs use the shared Git SSH format."""
        if value is None:
            return value

        if not GIT_SSH_URL_REGEX.match(value):
            raise ValueError(
                "Must be a valid Git SSH URL (e.g., git+ssh://git@github.com/org/repo.git)"
            )

        return value


# Params
class WorkspaceCreate(Schema):
    name: str = Field(..., min_length=1, max_length=100)
    settings: WorkspaceSettingsUpdate | None = None
    organization_id: OrganizationID | None = None


class WorkspaceUpdate(Schema):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    settings: WorkspaceSettingsUpdate | None = None


class WorkspaceSearch(Schema):
    name: str | None = None


# Responses
class WorkspaceReadMinimal(Schema):
    id: WorkspaceID
    name: str


class WorkspaceMemberStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    INVITED = "invited"


class WorkspaceMember(Schema):
    user_id: UserID | None = None
    invitation_id: UUID | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: EmailStr
    role_name: str
    status: WorkspaceMemberStatus = WorkspaceMemberStatus.ACTIVE
    token: str | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None


class WorkspaceRead(Schema):
    id: WorkspaceID
    name: str
    settings: WorkspaceSettingsRead | None = None
    organization_id: OrganizationID


WorkspaceSettingsRead.model_rebuild()
WorkspaceSettingsUpdate.model_rebuild()


# === Membership === #
class WorkspaceMembershipCreate(Schema):
    user_id: UserID
    role_id: str | None = (
        None  # UUID as string; defaults to workspace-editor if not provided
    )


class WorkspaceMembershipRead(Schema):
    user_id: UserID
    workspace_id: WorkspaceID


# === Invitation === #
class WorkspaceInvitationCreate(Schema):
    """Request schema for creating a workspace invitation."""

    email: EmailStr
    role_id: str  # UUID as string for API compatibility


class WorkspaceInvitationRead(Schema):
    """Response schema for a workspace invitation."""

    id: InvitationID
    workspace_id: WorkspaceID
    email: EmailStr
    role_id: str
    role_name: str
    role_slug: str | None = None
    status: InvitationStatus
    invited_by: UserID | None
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime
    token: str | None = None


class WorkspaceInvitationList(Schema):
    """Query params for listing workspace invitations."""

    status: InvitationStatus | None = None


class WorkspaceInvitationReadMinimal(Schema):
    """Public token lookup response for workspace invitation acceptance page."""

    workspace_id: WorkspaceID
    workspace_name: str
    organization_name: str
    inviter_name: str | None = None
    inviter_email: str | None = None
    role_name: str
    status: InvitationStatus
    expires_at: datetime
    email_matches: bool | None = None


class WorkspaceInvitationAccept(Schema):
    """Request body for accepting a workspace invitation via token."""

    token: str


# === Unified Add Member === #
class WorkspaceAddMemberRequest(Schema):
    """Request schema for adding a member to a workspace.

    The backend resolves whether to create a direct membership
    (if the email belongs to an existing org member) or an invitation.
    """

    email: EmailStr
    role_id: str  # UUID as string


class WorkspaceAddMemberResponse(Schema):
    """Response schema indicating which path was taken."""

    outcome: Literal["membership_created", "invitation_created"]
    invitation: WorkspaceInvitationRead | None = None
